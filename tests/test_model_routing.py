from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_away_message.cli import _model_clients, cli
from agent_away_message.config import ConfigError, default_settings, load_settings
from agent_away_message.generation import CommandClient, ModelRequestError


def _config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _command(executable: str = "/private/adapter", extra: str = "") -> str:
    return f'''schema_version = 2
[stage_one]
backend = "command"
[stage_one.command]
executable = "{executable}"
args = ["--literal"]
pass_environment = []
{extra}
'''


def test_defaults_remain_independent_local_routes() -> None:
    settings = default_settings()
    assert settings.stage_one.kind == settings.stage_two.kind == "local"


def test_schema_two_command_requires_absolute_literal_configuration(
    tmp_path: Path,
) -> None:
    settings = load_settings(_config(tmp_path / "ok.toml", _command()))
    assert settings.stage_one.kind == "command"
    assert settings.stage_one.args == ("--literal",)
    for executable in ("adapter", "", "sh -c adapter"):
        with pytest.raises(ConfigError):
            load_settings(
                _config(tmp_path / f"{len(executable)}.toml", _command(executable))
            )
    with pytest.raises(ConfigError):
        load_settings(_config(tmp_path / "old.toml", "schema_version = 1\n"))


def test_command_stage_one_requires_consent_and_stage_two_does_not(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path / "config.toml", _command())
    with pytest.raises(Exception, match="allow_remote_context"):
        _model_clients(load_settings(config))
    second = _config(
        tmp_path / "second.toml",
        """schema_version = 2
[stage_two]
backend = "command"
[stage_two.command]
executable = "/private/adapter"
""",
    )
    assert _model_clients(load_settings(second))[1]


def test_command_protocol_isolated_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr("agent_away_message.generation.os.killpg", lambda *_: None)

    class Process:
        pid = 22
        returncode = 0

        def communicate(
            self, request: bytes | None = None, **kwargs: object
        ) -> tuple[bytes, bytes]:
            seen["request"] = request
            seen.update(kwargs)
            assert Path(str(seen["cwd"])).is_dir()
            return (
                b'{"schema_version":1,"completion":"{\\"state\\":\\"working\\"}"}',
                b"child secret",
            )

    def popen(args: list[str], **kwargs: object) -> Process:
        seen["args"] = args
        seen.update(kwargs)
        return Process()

    prompt = "TOP SECRET source"
    output = CommandClient(
        "/private/adapter", ("--literal",), popen_factory=popen
    ).complete(prompt)
    assert output == '{"state":"working"}'
    assert prompt not in " ".join(seen["args"])
    assert json.loads(seen["request"])["prompt"] == prompt
    assert seen["env"] == {} and seen["stderr"] is subprocess.DEVNULL
    workspace = Path(str(seen["cwd"]))
    assert not workspace.exists()


@pytest.mark.parametrize(
    "reply",
    [
        b"bad",
        b"{}",
        b'{"schema_version":2,"completion":"x"}',
        b'{"schema_version":1,"completion":1}',
        b"\xff",
        b"x" * 65_537,
    ],
)
def test_command_rejects_bad_protocol(
    reply: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agent_away_message.generation.os.killpg", lambda *_: None)

    class Process:
        pid = 2
        returncode = 0

        def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            return reply, b"secret"

    with pytest.raises(ModelRequestError) as error:
        CommandClient(
            "/private/adapter", popen_factory=lambda *_a, **_k: Process()
        ).complete("secret")
    assert "secret" not in str(error.value)


def test_command_rejects_embedded_nul_process_strings(tmp_path: Path) -> None:
    for field, value in (
        ("executable", "/private/adapter\\u0000secret"),
        ("args", '["--literal\\u0000secret"]'),
        ("pass_environment", '["TOKEN\\u0000secret"]'),
    ):
        if field == "executable":
            text = _command(value)
        else:
            text = _command(extra=f"{field} = {value}\\n")
        with pytest.raises(ConfigError) as error:
            load_settings(_config(tmp_path / f"{field}.toml", text))
        assert "secret" not in str(error.value)


def test_command_launch_value_error_is_redacted() -> None:
    with pytest.raises(ModelRequestError) as error:
        CommandClient(
            "/private/adapter",
            popen_factory=lambda *_a, **_k: (_ for _ in ()).throw(ValueError("secret")),
        ).complete("secret")
    assert "secret" not in str(error.value)


def test_command_streaming_output_cap_uses_real_subprocess() -> None:
    with pytest.raises(ModelRequestError) as error:
        CommandClient(
            sys.executable,
            ("-c", "import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()"),
            timeout_seconds=5,
        ).complete("secret")
    assert "secret" not in str(error.value)


def test_command_timeout_covers_large_request_when_child_never_reads_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("COMMAND_CHILD_PID_FILE", str(pid_file))
    child = "import time; time.sleep(60)"
    program = (
        "import os, pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "pathlib.Path(os.environ['COMMAND_CHILD_PID_FILE']).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(ModelRequestError):
        CommandClient(
            sys.executable,
            ("-c", program),
            timeout_seconds=0.2,
            pass_environment=("COMMAND_CHILD_PID_FILE",),
        ).complete("x" * 1_000_000)
    assert time.monotonic() - started < 2
    _assert_process_gone(int(pid_file.read_text(encoding="utf-8")))


def test_command_reads_early_output_while_streaming_large_request() -> None:
    response = json.dumps({"schema_version": 1, "completion": "ok"})
    program = (
        "import json, sys; "
        f"sys.stdout.write({response!r}); sys.stdout.flush(); "
        "json.load(sys.stdin)"
    )
    assert (
        CommandClient(sys.executable, ("-c", program), timeout_seconds=2).complete(
            "x" * 1_000_000
        )
        == "ok"
    )


def _leader_with_detached_descendant(exit_code: int) -> str:
    child = "import time; time.sleep(60)"
    response = json.dumps({"schema_version": 1, "completion": "ok"})
    return (
        "import os, pathlib, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "pathlib.Path(os.environ['COMMAND_CHILD_PID_FILE']).write_text(str(child.pid)); "
        f"print({response!r}); sys.stdout.flush(); sys.exit({exit_code})"
    )


def _assert_process_gone(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail(f"descendant process {pid} survived command cleanup")


def test_command_success_cleans_detached_same_group_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("COMMAND_CHILD_PID_FILE", str(pid_file))
    result = CommandClient(
        sys.executable,
        ("-c", _leader_with_detached_descendant(0)),
        pass_environment=("COMMAND_CHILD_PID_FILE",),
    ).complete("secret")
    assert result == "ok"
    _assert_process_gone(int(pid_file.read_text(encoding="utf-8")))


def test_command_nonzero_cleans_detached_same_group_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("COMMAND_CHILD_PID_FILE", str(pid_file))
    with pytest.raises(ModelRequestError):
        CommandClient(
            sys.executable,
            ("-c", _leader_with_detached_descendant(3)),
            pass_environment=("COMMAND_CHILD_PID_FILE",),
        ).complete("secret")
    _assert_process_gone(int(pid_file.read_text(encoding="utf-8")))


def test_command_launch_and_nonzero_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_away_message.generation.os.killpg", lambda *_: None)
    with pytest.raises(ModelRequestError) as launch:
        CommandClient(
            "/private/adapter",
            popen_factory=lambda *_a, **_k: (_ for _ in ()).throw(OSError("secret")),
        ).complete("secret")
    assert "secret" not in str(launch.value)

    class Nonzero:
        pid = 4
        returncode = 1

        def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"secret", b"secret"

    with pytest.raises(ModelRequestError) as nonzero:
        CommandClient(
            "/private/adapter", popen_factory=lambda *_a, **_k: Nonzero()
        ).complete("secret")
    assert "secret" not in str(nonzero.value)


def test_command_failure_timeout_reaps_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, int]] = []

    class Process:
        pid = 5
        returncode = 0
        calls = 0

        def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("/secret/path", 1, output=b"secret")
            return b"", b""

    monkeypatch.setattr(
        "agent_away_message.generation.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    with pytest.raises(ModelRequestError) as error:
        CommandClient(
            "/secret/path", popen_factory=lambda *_a, **_k: Process()
        ).complete("secret")
    assert killed == [(5, 9)] and "secret" not in str(error.value)


def test_simulate_history_rejects_command_before_source_extraction(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path / "config.toml",
        """schema_version = 2
context_mode = "candid"
[privacy]
allow_remote_context = true
[stage_one]
backend = "command"
[stage_one.command]
executable = "/private/adapter"
""",
    )
    result = CliRunner().invoke(
        cli, ["--config", str(config), "simulate-history", "--max-examples", "0"]
    )
    assert result.exit_code != 0
    assert "requires a local stage one backend" in result.output


def test_doctor_redacts_command_details(tmp_path: Path) -> None:
    config = _config(
        tmp_path / "config.toml",
        _command("/private/adapter", "timeout_seconds = 3\n")
        + """
[privacy]
allow_remote_context = true
[stage_two]
backend = "command"
[stage_two.command]
executable = "/private/adapter"
""",
    )
    result = CliRunner().invoke(
        cli, ["--config", str(config), "--context-mode", "candid", "--json", "doctor"]
    )
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["stage_one_backend"] == body["stage_two_backend"] == "command"
    assert body["raw_context_shared_with_external_backend"] is True
    assert "/private/adapter" not in result.output
