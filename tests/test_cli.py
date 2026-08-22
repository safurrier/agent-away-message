from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import agent_away_message.cli as cli_module
from agent_away_message.cli import cli
from agent_away_message.daemon import PreviewGenerationError
from agent_away_message.generation import MalformedCandidateError
from agent_away_message.publisher import DiscordAccount


def test_ingest_and_status_return_aggregate_json(tmp_path) -> None:
    runner = CliRunner()
    args = ["--state-dir", str(tmp_path), "--json"]

    ingested = runner.invoke(
        cli,
        [
            *args,
            "ingest",
            "--harness",
            "pi",
            "--session-id",
            "agent_1",
            "--event",
            "started",
        ],
    )
    status = runner.invoke(cli, [*args, "status"])

    assert ingested.exit_code == 0
    assert json.loads(status.output)["active_agents"] == 1
    persisted = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "agent_1" not in persisted


def test_discord_accounts_has_deterministic_human_and_json_output(
    monkeypatch,
) -> None:
    accounts = [
        DiscordAccount(0, "personal-id", "__chef__", "Chef"),
        DiscordAccount(1, "work-id", "alex.f", "alex"),
    ]
    monkeypatch.setattr(
        cli_module, "discover_discord_accounts", lambda _client_id: accounts
    )

    human = CliRunner().invoke(
        cli, ["discord-accounts", "--discord-client-id", "application-id"]
    )
    structured = CliRunner().invoke(
        cli,
        ["--json", "discord-accounts", "--discord-client-id", "application-id"],
    )

    assert human.exit_code == 0
    assert human.output.splitlines() == [
        "PIPE\tUSER ID\tUSERNAME\tDISPLAY NAME",
        "0\tpersonal-id\t__chef__\tChef",
        "1\twork-id\talex.f\talex",
    ]
    assert structured.exit_code == 0
    assert json.loads(structured.output) == {
        "accounts": [
            {
                "display_name": "Chef",
                "pipe": 0,
                "user_id": "personal-id",
                "username": "__chef__",
            },
            {
                "display_name": "alex",
                "pipe": 1,
                "user_id": "work-id",
                "username": "alex.f",
            },
        ]
    }


def test_daemon_passes_discord_user_id_to_rpc_client(tmp_path, monkeypatch) -> None:
    constructed: list[tuple[str, str | None]] = []

    class FakeClient:
        def __init__(self, client_id: str, user_id: str | None) -> None:
            constructed.append((client_id, user_id))

    class FakePublisher:
        def __init__(self, _client: object) -> None:
            pass

        def publish(self, _presence: object) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "PypresenceClient", FakeClient)
    monkeypatch.setattr(cli_module, "DiscordPublisher", FakePublisher)
    monkeypatch.setattr(
        cli_module,
        "refresh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--publication-mode",
            "discord",
            "daemon",
            "--once",
            "--discord-client-id",
            "application-id",
            "--discord-user-id",
            "work-id",
        ],
    )

    assert result.exit_code == 0
    assert constructed == [("application-id", "work-id")]


def test_isolated_preview_constructs_no_discord_publisher_or_rpc(
    tmp_path, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("preview must not construct Discord transport")

    monkeypatch.setattr(cli_module, "DiscordPublisher", forbidden)
    monkeypatch.setattr(cli_module, "PypresenceClient", forbidden)
    result = CliRunner().invoke(
        cli, ["--state-dir", str(tmp_path), "--json", "preview"]
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "active": False,
        "details": None,
        "state": None,
    }


@pytest.mark.parametrize(
    ("config_bytes", "command", "payload", "event"),
    [
        (
            None,
            "codex-hook",
            '{"hook_event_name":"Stop","session_id":"agent_1"}',
            "codex.stop",
        ),
        (
            None,
            "pi-hook",
            '{"event":"session_shutdown","session_id":"agent_1"}',
            "pi.session_shutdown",
        ),
        (
            b"schema_version = 2\n[stage_one.local]\nurl = 'http://[::1'\n",
            "codex-hook",
            '{"hook_event_name":"Stop","session_id":"agent_1"}',
            "codex.stop",
        ),
        (
            b"schema_version = 2\n[stage_one.local]\nurl = 'http://[::1'\n",
            "pi-hook",
            '{"event":"session_shutdown","session_id":"agent_1"}',
            "pi.session_shutdown",
        ),
        (
            b"\xff\xfeinvalid",
            "codex-hook",
            '{"hook_event_name":"Stop","session_id":"agent_1"}',
            "codex.stop",
        ),
        (
            b"\xff\xfeinvalid",
            "pi-hook",
            '{"event":"session_shutdown","session_id":"agent_1"}',
            "pi.session_shutdown",
        ),
    ],
)
def test_native_hook_stays_silent_and_records_lifecycle_when_config_is_invalid(
    tmp_path, config_bytes: bytes | None, command: str, payload: str, event: str
) -> None:
    config = tmp_path / "invalid.toml"
    if config_bytes is not None:
        config.write_bytes(config_bytes)
    result = CliRunner().invoke(
        cli,
        ["--config", str(config), "--state-dir", str(tmp_path), command],
        input=payload,
    )

    assert result.exit_code == 0
    assert result.output == result.stderr == ""
    assert f'"event":"{event}"' in (tmp_path / "events.jsonl").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("config_bytes", "expected_error"),
    [
        (None, "application config does not exist"),
        (
            b"schema_version = 2\n[stage_one.local]\nurl = 'http://[::1'\n",
            "stage_one.local.url is invalid",
        ),
        (b"\xff\xfeinvalid", "application config is invalid"),
    ],
)
@pytest.mark.parametrize("command", ["doctor", "daemon --once"])
def test_non_hook_invalid_config_fails_closed_without_config_contents(
    tmp_path, config_bytes: bytes | None, expected_error: str, command: str
) -> None:
    config = tmp_path / "invalid.toml"
    if config_bytes is not None:
        config.write_bytes(config_bytes)
    result = CliRunner().invoke(cli, ["--config", str(config), *command.split()])

    assert result.exit_code != 0
    assert result.output == f"Error: {expected_error}\n"


def test_codex_hook_fails_soft_without_persisting_payload(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--state-dir", str(tmp_path), "--json", "codex-hook"],
        input='{"event":"unknown","session_id":"agent_1","prompt":"secret"}',
    )

    assert result.exit_code == 0
    assert result.output == ""
    assert not (tmp_path / "events.jsonl").exists()


def test_successful_codex_stop_is_silent_and_persists_lifecycle(tmp_path) -> None:
    result = CliRunner().invoke(
        cli,
        ["--state-dir", str(tmp_path), "codex-hook"],
        input='{"hook_event_name":"Stop","session_id":"agent_1"}',
    )

    assert result.exit_code == 0
    assert result.output == result.stderr == ""
    assert '"event":"codex.stop"' in (tmp_path / "events.jsonl").read_text(
        encoding="utf-8"
    )


def test_native_hooks_fail_silently_when_state_directory_is_unwritable() -> None:
    runner = CliRunner()
    cases = (
        (
            "codex-hook",
            '{"hook_event_name":"Stop","session_id":"agent_1"}',
        ),
        ("pi-hook", '{"event":"session_shutdown","session_id":"agent_1"}'),
    )

    for command, payload in cases:
        result = runner.invoke(
            cli,
            ["--state-dir", "/dev/null/agent-away-message", command],
            input=payload,
        )

        assert result.exit_code == 0
        assert result.output == ""
        assert result.stderr == ""


def test_setup_dry_run_keeps_existing_hook_entry(tmp_path) -> None:
    config = tmp_path / "hooks.json"
    config.write_text(
        '{"hooks":{"existing-tool":{"command":"existing-tool"}}}',
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--json", "setup", "--config", str(config), "--dry-run"]
    )

    assert result.exit_code == 0
    assert json.loads(config.read_text(encoding="utf-8"))["hooks"] == {
        "existing-tool": {"command": "existing-tool"}
    }


def test_fixture_inspection_rejects_forbidden_source_fields(tmp_path) -> None:
    fixture = tmp_path / "unsafe.jsonl"
    fixture.write_text(
        '{"schema_version":1,"harness":"pi","session_id":"agent_1",'
        '"event":"started","occurred_at":"2026-01-01T00:00:00+00:00",'
        '"expires_at":"2026-01-01T00:05:00+00:00","provenance":"test",'
        '"prompt":"private source text"}\n',
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["fixture", "inspect", str(fixture)])

    assert result.exit_code != 0
    assert "private source text" not in result.output


def test_unsafe_activity_hint_is_rejected(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "ingest",
            "--harness",
            "codex",
            "--session-id",
            "agent_1",
            "--event",
            "started",
            "--activity",
            "edit /private/project",
        ],
    )

    assert result.exit_code != 0


def test_continuous_daemon_quarantines_invalid_public_history(
    tmp_path, monkeypatch
) -> None:
    history = tmp_path / "message-history.json"
    history.write_text('[{"state":"taking a short break"}]', encoding="utf-8")

    def stop_loop(_interval):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.time, "sleep", stop_loop)
    result = CliRunner().invoke(
        cli,
        ["--state-dir", str(tmp_path), "--json", "daemon", "--interval", "1"],
    )

    assert result.exit_code == 0
    payloads = [json.loads(line) for line in result.output.splitlines()]
    assert payloads[0] == {"history_quarantined": True}
    assert not history.exists()
    assert len(list(tmp_path.glob("message-history.invalid-*.json"))) == 1


def test_continuous_candid_daemon_quarantines_invalid_activity_window(
    tmp_path, monkeypatch
) -> None:
    activity = tmp_path / "activity-window.json"
    activity.write_text('{"schema_version":2,"activities":[]}', encoding="utf-8")

    monkeypatch.setattr(cli_module, "_open_context_socket", lambda _settings: None)
    monkeypatch.setattr(
        cli_module.time,
        "sleep",
        lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--context-mode",
            "candid",
            "--json",
            "daemon",
            "--interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output.splitlines()[0]) == {"activity_quarantined": True}
    assert not activity.exists()
    assert len(list(tmp_path.glob("activity-window.invalid-*.json"))) == 1


def test_continuous_candid_daemon_quarantines_unrepresentable_activity_timestamp(
    tmp_path, monkeypatch
) -> None:
    activity = tmp_path / "activity-window.json"
    activity.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activities": [
                    {
                        "session_id": "a" * 32,
                        "turn_token": "b" * 32,
                        "text": "updating the adapter",
                        "phase": "settled",
                        "observed_at": "0001-01-01T00:00:00+23:59",
                        "expires_at": "0001-01-01T00:01:00+23:59",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_module, "_open_context_socket", lambda _settings: None)
    monkeypatch.setattr(
        cli_module.time,
        "sleep",
        lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--context-mode",
            "candid",
            "--json",
            "daemon",
            "--interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    payloads = [json.loads(line) for line in result.output.splitlines()]
    assert payloads == [
        {"activity_quarantined": True},
        {
            "stage_one_backend": "local",
            "stage_two_backend": "local",
            "raw_context_shared_with_external_backend": False,
        },
        {"active": False, "details": None, "state": None},
    ]
    assert not activity.exists()
    assert len(list(tmp_path.glob("activity-window.invalid-*.json"))) == 1


def test_continuous_candid_daemon_quarantines_oversized_valid_activity_window(
    tmp_path, monkeypatch
) -> None:
    activity = tmp_path / "activity-window.json"
    valid_activity = {
        "session_id": "a" * 32,
        "turn_token": "b" * 32,
        "text": "updating the adapter",
        "phase": "settled",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-01T00:30:00+00:00",
    }
    activity.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activities": [valid_activity for _ in range(33)],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_module, "_open_context_socket", lambda _settings: None)
    monkeypatch.setattr(
        cli_module.time,
        "sleep",
        lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--context-mode",
            "candid",
            "--json",
            "daemon",
            "--interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    payloads = [json.loads(line) for line in result.output.splitlines()]
    assert payloads == [
        {"activity_quarantined": True},
        {
            "stage_one_backend": "local",
            "stage_two_backend": "local",
            "raw_context_shared_with_external_backend": False,
        },
        {"active": False, "details": None, "state": None},
    ]
    assert not activity.exists()
    assert len(list(tmp_path.glob("activity-window.invalid-*.json"))) == 1


def test_continuous_candid_daemon_quarantines_non_string_phase(
    tmp_path, monkeypatch
) -> None:
    activity = tmp_path / "activity-window.json"
    activity.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activities": [
                    {
                        "session_id": "a" * 32,
                        "turn_token": "b" * 32,
                        "text": "updating the adapter",
                        "phase": [],
                        "observed_at": "2026-01-01T00:00:00+00:00",
                        "expires_at": "2026-01-01T00:30:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_module, "_open_context_socket", lambda _settings: None)
    monkeypatch.setattr(
        cli_module.time,
        "sleep",
        lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--context-mode",
            "candid",
            "--json",
            "daemon",
            "--interval",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output.splitlines()[0]) == {"activity_quarantined": True}
    assert not activity.exists()
    assert len(list(tmp_path.glob("activity-window.invalid-*.json"))) == 1


def test_preview_and_continuous_diagnostics_expose_safe_rejection_code(
    tmp_path, monkeypatch
) -> None:
    def fail(*_args, **_kwargs):
        raise PreviewGenerationError(
            MalformedCandidateError("rejected prose must not leak")
        )

    monkeypatch.setattr(cli_module, "refresh", fail)
    preview = CliRunner().invoke(cli, ["--state-dir", str(tmp_path), "preview"])
    assert preview.exit_code != 0
    assert "malformed" in preview.output
    assert "rejected prose" not in preview.output

    monkeypatch.setattr(
        cli_module.time,
        "sleep",
        lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    daemon = CliRunner().invoke(
        cli, ["--state-dir", str(tmp_path), "--json", "daemon", "--interval", "1"]
    )
    assert daemon.exit_code == 0
    assert json.loads(daemon.output.splitlines()[-1])["generation_error"] == "malformed"
    assert "PreviewGenerationError" not in daemon.output


def test_continuous_daemon_clears_presence_and_survives_outer_failure(
    tmp_path, monkeypatch
) -> None:
    class RecordingPublisher:
        def __init__(self) -> None:
            self.values: list[object] = []
            self.closed = False

        def publish(self, presence) -> None:
            self.values.append(presence)

        def close(self) -> None:
            self.closed = True

    publisher = RecordingPublisher()
    monkeypatch.setattr(cli_module, "MemoryPublisher", lambda: publisher)

    def fail_refresh(*_args, **_kwargs):
        raise OSError("discord unavailable")

    monkeypatch.setattr(cli_module, "refresh", fail_refresh)

    def stop_loop(_interval):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.time, "sleep", stop_loop)

    result = CliRunner().invoke(
        cli,
        ["--state-dir", str(tmp_path), "--json", "daemon", "--interval", "1"],
    )

    assert result.exit_code == 0
    assert publisher.values == [None]
    assert publisher.closed is True
    assert "OSError" in result.output
