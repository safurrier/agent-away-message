from __future__ import annotations

import json
import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which
from typing import ClassVar

from click.testing import CliRunner

from agent_away_message.cli import cli
from agent_away_message.generation import OpenAICompatibleClient


class CompletionHandler(BaseHTTPRequestHandler):
    prompt = ""
    payloads: ClassVar[list[dict[str, object]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        type(self).payloads.append(payload)
        type(self).prompt = payload["messages"][0]["content"]
        body = json.dumps(
            {
                "id": "local-test",
                "object": "chat.completion",
                "created": 0,
                "model": "fake-local",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"state":"herding the usual suspects"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_openai_sdk_calls_fake_local_http_server() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), CompletionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        seeded = OpenAICompatibleClient(
            f"http://127.0.0.1:{port}/v1", "fake-local", seed=42
        ).complete("aggregate facts only")
        unseeded = OpenAICompatibleClient(
            f"http://127.0.0.1:{port}/v1", "fake-local"
        ).complete("aggregate facts only")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert seeded == unseeded == '{"state":"herding the usual suspects"}'
    assert CompletionHandler.prompt == "aggregate facts only"
    seeded_payload, unseeded_payload = CompletionHandler.payloads[-2:]
    assert seeded_payload["temperature"] == 0.2
    assert seeded_payload["seed"] == 42
    assert seeded_payload["response_format"] == {"type": "json_object"}
    assert "seed" not in unseeded_payload


def test_pi_setup_and_doctor_are_idempotent_and_truthful(tmp_path: Path) -> None:
    extension = tmp_path / "extensions" / "away-message.ts"
    runner = CliRunner()
    setup_args = ["--json", "setup", "--pi-extension", str(extension)]

    first = runner.invoke(cli, setup_args)
    second = runner.invoke(cli, setup_args)
    doctor = runner.invoke(cli, ["--json", "doctor", "--pi-extension", str(extension)])

    assert first.exit_code == second.exit_code == doctor.exit_code == 0
    assert json.loads(first.output)["pi"] is True
    assert json.loads(second.output)["pi"] is False
    assert json.loads(doctor.output)["pi_extension"] is True
    source = extension.read_text(encoding="utf-8")
    assert "export default function" in source
    assert "spawnSync" not in source
    assert "spawn(" in source
    assert "process.pid" not in source
    assert "getSessionId()" in source
    for event in (
        "session_start",
        "input",
        "agent_start",
        "tool_call",
        "agent_end",
        "session_shutdown",
    ):
        assert f'pi.on("{event}"' in source


def test_doctor_rejects_pi_extension_with_stale_embedded_executable(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "away-message.ts"
    runner = CliRunner()
    setup = runner.invoke(cli, ["setup", "--pi-extension", str(extension)])
    source = extension.read_text(encoding="utf-8")
    bridge_line = next(
        line
        for line in source.splitlines()
        if line.startswith("const BRIDGE_EXECUTABLE")
    )
    extension.write_text(
        source.replace(
            bridge_line,
            'const BRIDGE_EXECUTABLE = "/missing/agent-away-message";',
        ),
        encoding="utf-8",
    )

    doctor = runner.invoke(cli, ["--json", "doctor", "--pi-extension", str(extension)])

    assert setup.exit_code == doctor.exit_code == 0
    assert json.loads(doctor.output)["pi_extension"] is False


def test_installed_pi_extension_uses_candid_input_and_drains_shutdown(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "extension.ts"
    state_dir = tmp_path / "state with spaces"
    setup = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "--context-mode",
            "candid",
            "setup",
            "--pi-extension",
            str(extension),
        ],
    )
    assert setup.exit_code == 0
    extension.write_text(
        extension.read_text(encoding="utf-8")
        .replace("const HOOK_TIMEOUT_MS = 1_000;", "const HOOK_TIMEOUT_MS = 15;")
        .replace(
            'import { spawn } from "node:child_process";',
            "const { spawn } = globalThis.__childProcess;",
        ),
        encoding="utf-8",
    )
    script = tmp_path / "exercise.mjs"
    script.write_text(
        """
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
const plans = [
  { status: 0 }, { status: 0 }, { status: 0 }, { hang: true }, { status: 0 }, { status: 0 },
];
const calls = [];
let killed = 0;
const stateDir = __STATE_DIR__;
globalThis.__childProcess = { spawn: (command, argv) => {
  const child = new EventEmitter();
  const plan = plans.shift();
  child.kill = () => { killed += 1; child.emit("close", null); };
  child.stdin = { end: (raw) => {
    calls.push({ command, argv, payload: JSON.parse(raw) });
    if (plan.error) setTimeout(() => child.emit("error", new Error("missing")), 0);
    else if (!plan.hang) setTimeout(() => child.emit("close", plan.status), 0);
  }};
  return child;
}};
const extension = await import("./extension.ts");
const handlers = {};
const context = { sessionManager: { getSessionId: () => "stable-pi-session" } };
extension.default({ on: (event, handler) => { handlers[event] = handler; } });
assert.equal(handlers.session_start({}, context), undefined);
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(handlers.input({ text: "private input", source: "interactive" }, context), undefined);
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(handlers.input({ text: "extension private input", source: "extension" }, context), undefined);
assert.equal(handlers.agent_start({}, context), undefined);
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(handlers.tool_call({}, context), undefined);
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(handlers.agent_end({ messages: [] }, context), undefined);
assert.equal(handlers.agent_settled({}, context), undefined);
const drained = handlers.session_shutdown({}, context);
assert.ok(drained instanceof Promise);
await drained;
assert.deepEqual(calls.map((call) => call.payload.event), [
  "session_start", "input", "agent_start", "tool_call", "agent_end", "session_shutdown",
]);
assert.ok(calls.every((call) => call.command.endsWith("/agent-away-message")));
assert.deepEqual(calls.map((call) => call.argv), [
  ["--state-dir", stateDir, "pi-hook"],
  ["--state-dir", stateDir, "--context-mode", "candid", "pi-hook"],
  ["--state-dir", stateDir, "pi-hook"],
  ["--state-dir", stateDir, "pi-hook"],
  ["--state-dir", stateDir, "--context-mode", "candid", "pi-hook"],
  ["--state-dir", stateDir, "pi-hook"],
]);
assert.equal(calls[1].payload.text, "private input");
assert.ok(calls.every((call) => call.payload.session_id === "stable-pi-session"));
assert.equal(killed, 1);
""".replace("__STATE_DIR__", json.dumps(str(state_dir))).strip(),
        encoding="utf-8",
    )

    node = which("node")
    assert node is not None
    result = subprocess.run(  # noqa: S603
        [node, "--experimental-strip-types", str(script)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_codex_setup_encodes_candid_mode_at_the_hook_boundary(
    tmp_path: Path,
) -> None:
    config = tmp_path / "hooks.json"
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path / "state with spaces"),
            "--context-mode",
            "candid",
            "--json",
            "setup",
            "--codex-config",
            str(config),
        ],
    )

    assert result.exit_code == 0
    hooks = json.loads(config.read_text(encoding="utf-8"))["hooks"]
    command = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    parts = shlex.split(command)
    assert Path(parts[0]).name == "agent-away-message"
    assert parts[1:3] == ["--state-dir", str(tmp_path / "state with spaces")]
    assert parts[3:5] == ["--context-mode", "candid"]
    assert command.endswith("codex-hook")


def test_doctor_rejects_context_mode_drift(tmp_path: Path) -> None:
    config = tmp_path / "hooks.json"
    extension = tmp_path / "extension.ts"
    runner = CliRunner()
    setup = runner.invoke(
        cli,
        [
            "--context-mode",
            "candid",
            "setup",
            "--codex-config",
            str(config),
            "--pi-extension",
            str(extension),
        ],
    )

    matching = runner.invoke(
        cli,
        [
            "--context-mode",
            "candid",
            "--json",
            "doctor",
            "--codex-config",
            str(config),
            "--pi-extension",
            str(extension),
        ],
    )
    mismatched = runner.invoke(
        cli,
        [
            "--context-mode",
            "generic",
            "--json",
            "doctor",
            "--codex-config",
            str(config),
            "--pi-extension",
            str(extension),
        ],
    )

    assert setup.exit_code == matching.exit_code == mismatched.exit_code == 0
    assert json.loads(matching.output)["codex_hook"] is True
    assert json.loads(matching.output)["pi_extension"] is True
    assert json.loads(mismatched.output)["codex_hook"] is False
    assert json.loads(mismatched.output)["pi_extension"] is False


def test_installed_pi_candid_extension_emits_bounded_turn_context(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "extension.ts"
    state_dir = tmp_path / "state"
    setup = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "--context-mode",
            "candid",
            "setup",
            "--pi-extension",
            str(extension),
        ],
    )
    assert setup.exit_code == 0
    extension.write_text(
        extension.read_text(encoding="utf-8").replace(
            'import { spawn } from "node:child_process";',
            "const { spawn } = globalThis.__childProcess;",
        ),
        encoding="utf-8",
    )
    script = tmp_path / "exercise-candid.mjs"
    script.write_text(
        """
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
const calls = [];
globalThis.__childProcess = { spawn: (command, argv) => {
  const child = new EventEmitter();
  child.kill = () => child.emit("close", null);
  child.stdin = { end: (raw) => {
    calls.push({ command, argv, payload: JSON.parse(raw) });
    setTimeout(() => child.emit("close", 0), 0);
  }};
  return child;
}};
const extension = await import("./extension.ts");
const handlers = {};
const context = { sessionManager: { getSessionId: () => "stable-pi-session" } };
extension.default({ on: (event, handler) => { handlers[event] = handler; } });
handlers.session_start({}, context);
await new Promise((resolve) => setTimeout(resolve, 0));
handlers.input({ text: "Investigate private adapter behavior", source: "interactive" }, context);
await new Promise((resolve) => setTimeout(resolve, 0));
handlers.agent_end({ messages: [
  { role: "assistant", content: "OLDER_COMPLETION" },
] }, context);
handlers.agent_end({ messages: [
  { role: "tool", content: "PRIVATE_TOOL_RESULT" },
  { role: "assistant", content: [{ type: "toolCall", command: "PRIVATE_COMMAND" }, { type: "text", text: "Updated it and checked installation." }] },
] }, context);
handlers.agent_settled({}, context);
await handlers.session_shutdown({}, context);
const input = calls.find((call) => call.payload.event === "input");
const ended = calls.find((call) => call.payload.event === "agent_end");
assert.ok(input);
assert.equal(input.payload.text, "Investigate private adapter behavior");
assert.deepEqual(input.argv.slice(-3), ["--context-mode", "candid", "pi-hook"]);
assert.ok(ended);
assert.equal(calls.filter((call) => call.payload.event === "agent_end").length, 1);
assert.deepEqual(ended.argv.slice(-3), ["--context-mode", "candid", "pi-hook"]);
assert.equal(
  ended.payload.candid_context,
  "User: Investigate private adapter behavior\\nAssistant: Updated it and checked installation.",
);
assert.ok(!ended.payload.candid_context.includes("PRIVATE_TOOL_RESULT"));
assert.ok(!ended.payload.candid_context.includes("PRIVATE_COMMAND"));
assert.ok(!ended.payload.candid_context.includes("OLDER_COMPLETION"));
assert.ok(!ended.payload.candid_context.includes("RETRY_COMPLETION"));
assert.ok(calls.every((call) => call.payload.session_id === "stable-pi-session"));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603
        [which("node"), "--experimental-strip-types", str(script)],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_explicit_session_inspection_emits_structure_not_content(
    tmp_path: Path,
) -> None:
    session = tmp_path / "synthetic.jsonl"
    canary = "CANARY_PRIVATE_PROMPT"
    session.write_text(
        json.dumps({"type": "session_meta", "payload": canary})
        + "\n"
        + json.dumps({"type": "event_msg", "message": canary})
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli, ["--json", "session-inspect", "--harness", "codex", str(session)]
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "records": 2,
        "types": {"event_msg": 1, "session_meta": 1},
    }
    assert canary not in result.output


def test_pi_session_inspection_accepts_native_structural_types(tmp_path: Path) -> None:
    session = tmp_path / "synthetic-pi.jsonl"
    session.write_text(
        "\n".join(
            json.dumps({"type": item, "content": "CANARY"})
            for item in ("session", "message", "compaction", "session_info")
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli, ["--json", "session-inspect", "--harness", "pi", str(session)]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["records"] == 4
    assert "CANARY" not in result.output
