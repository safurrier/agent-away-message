from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from click.testing import CliRunner

import agent_away_message.cli as cli_module
import agent_away_message.history_simulation as simulation_module
from agent_away_message.cli import cli
from agent_away_message.generation import ModelRequestError
from agent_away_message.history_simulation import MAX_SOURCE_CHARS, simulate_history
from agent_away_message.models import ContextMode


class StageOne:
    def __init__(self, *, admit: bool = True) -> None:
        self.admit = admit
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Proposed public activity:" in prompt:
            return '{"pass":true,"reason":null}'
        return '{"text":"reviewing an integration"}' if self.admit else '{"text":null}'


class FailingStageOne:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        raise ModelRequestError("offline")


class StageTwo:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return '{"state":"apparently, integrations come with paperwork"}'


def write_jsonl(path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def codex_history(
    root, now: datetime, source: str = "fixed SecretVenusRepository integration"
) -> None:
    write_jsonl(
        root / "secret-session.jsonl",
        [
            {
                "timestamp": (now - timedelta(minutes=10)).isoformat(),
                "type": "event_msg",
                "payload": {"type": "task_started"},
            },
            {
                "timestamp": (now - timedelta(minutes=5)).isoformat(),
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": source},
            },
        ],
    )


def test_simulation_runs_end_to_end_and_returns_only_public_outputs(tmp_path) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    root = tmp_path / "private-codex-root"
    codex_history(root, now)
    malformed = root / "malformed.jsonl"
    malformed.write_text("not json\n", encoding="utf-8")
    old = root / "old.jsonl"
    old.write_text("not parsed\n", encoding="utf-8")
    os.utime(old, (0, 0))
    stage_one, stage_two = StageOne(), StageTwo()

    result = simulate_history(
        stage_one,
        stage_two,
        mode=ContextMode.CANDID,
        codex_root=root,
        pi_root=tmp_path / "missing",
        since=now - timedelta(hours=24),
        now=now,
        ttl_seconds=1_800,
        max_examples=1,
    )

    encoded = json.dumps(result)
    assert result["peak_agents"] == 1
    assert result["sources"]["malformed_or_unrecognized_skipped"] == 1
    assert result["sources"]["older_files_skipped"] == 1
    assert result["candidate_sampling"] == {
        "attempted": 1,
        "admitted": 1,
        "generic_fallbacks": 0,
        "generation_errors": 0,
    }
    example = result["replay_examples"][0]
    assert example["public_activity"] == "reviewing an integration"
    assert example["status"] == "apparently, integrations come with paperwork"
    assert example["generic_fallback"] is False
    assert "SecretVenusRepository" not in encoded
    assert "private-codex-root" not in encoded
    assert "SecretVenusRepository" not in stage_one.prompts[1]
    assert "SecretVenusRepository" not in stage_two.prompts[0]
    assert len(stage_one.prompts) == 2
    assert len(stage_two.prompts) == 1


def test_simulation_candid_abstention_generates_generic_status(tmp_path) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    root = tmp_path / "codex"
    codex_history(root, now, "ambiguous private source")
    stage_one, stage_two = StageOne(admit=False), StageTwo()

    result = simulate_history(
        stage_one,
        stage_two,
        mode=ContextMode.CANDID,
        codex_root=root,
        pi_root=tmp_path / "missing",
        since=now - timedelta(hours=1),
        now=now,
        ttl_seconds=1_800,
        max_examples=1,
    )

    example = result["replay_examples"][0]
    assert example["public_activity"] is None
    assert example["generic_fallback"] is True
    assert example["status"] == "apparently, integrations come with paperwork"
    assert "Approved candid activity" not in stage_two.prompts[0]
    assert len(stage_one.prompts) == 1
    assert len(stage_two.prompts) == 1


def test_pi_parser_bounds_source_and_excludes_tool_content(tmp_path) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    pi_root = tmp_path / "pi"
    write_jsonl(
        pi_root / "session.jsonl",
        [
            {
                "timestamp": (now - timedelta(minutes=1)).isoformat(),
                "type": "message",
                "message": {"role": "user", "content": "x" * 7_000},
            },
            {
                "timestamp": now.isoformat(),
                "type": "message",
                "message": {
                    "role": "assistant",
                    "stopReason": "stop",
                    "content": [{"type": "text", "text": "reviewed the integration"}],
                },
            },
            {
                "timestamp": now.isoformat(),
                "type": "message",
                "message": {"role": "toolResult", "content": "private tool result"},
            },
        ],
    )
    stage_one = StageOne()
    simulate_history(
        stage_one,
        StageTwo(),
        mode=ContextMode.CANDID,
        codex_root=tmp_path / "missing",
        pi_root=pi_root,
        since=now - timedelta(hours=1),
        now=now,
        ttl_seconds=1_800,
        max_examples=1,
    )
    source_suffix = stage_one.prompts[0].split("Context follows:\n", 1)[1]
    assert len(source_suffix) <= MAX_SOURCE_CHARS
    assert "private tool result" not in stage_one.prompts[0]


def test_simulate_history_cli_uses_both_routes_and_emits_raw_free_json(
    tmp_path, monkeypatch
) -> None:
    now = datetime.now(UTC)
    root = tmp_path / "private-history"
    codex_history(root, now)
    clients = (StageOne(), StageTwo())
    monkeypatch.setattr(cli_module, "_model_clients", lambda _settings: clients)

    result = CliRunner().invoke(
        cli,
        [
            "--context-mode",
            "candid",
            "--json",
            "simulate-history",
            "--since",
            "24h",
            "--codex-root",
            str(root),
            "--pi-root",
            str(tmp_path / "missing"),
            "--max-examples",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["replay_examples"][0]["status"]
    assert "SecretVenusRepository" not in result.output
    assert "private-history" not in result.output
    assert len(clients[0].prompts) == 2
    assert len(clients[1].prompts) == 1


def test_candid_transport_failure_fails_closed_without_stage_two_call(tmp_path) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    root = tmp_path / "codex"
    codex_history(root, now)
    stage_one, stage_two = FailingStageOne(), StageTwo()
    result = simulate_history(
        stage_one,
        stage_two,
        mode=ContextMode.CANDID,
        codex_root=root,
        pi_root=tmp_path / "missing",
        since=now - timedelta(hours=1),
        now=now,
        ttl_seconds=1_800,
        max_examples=1,
    )
    example = result["replay_examples"][0]
    assert example["status"] is None
    assert example["generic_fallback"] is False
    assert example["error"] == "ModelRequestError"
    assert stage_two.prompts == []


def test_generic_replay_never_extracts_source_text(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    pi_root = tmp_path / "pi"
    write_jsonl(
        pi_root / "session.jsonl",
        [
            {
                "timestamp": now.isoformat(),
                "type": "message",
                "message": {"role": "user", "content": "private source"},
            }
        ],
    )

    def forbidden(_content: object) -> str:
        raise AssertionError("generic replay extracted source text")

    monkeypatch.setattr(simulation_module, "_message_text", forbidden)
    result = simulate_history(
        FailingStageOne(),
        StageTwo(),
        mode=ContextMode.GENERIC,
        codex_root=tmp_path / "missing",
        pi_root=pi_root,
        since=now - timedelta(hours=1),
        now=now,
        ttl_seconds=1_800,
        max_examples=1,
    )
    assert result["replay_examples"][0]["public_activity"] is None
    assert "not extracted" in result["notes"][1]


def test_generic_replay_uses_timeline_and_zero_stage_one_calls(tmp_path) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    root = tmp_path / "codex"
    codex_history(root, now)
    stage_one, stage_two = FailingStageOne(), StageTwo()
    result = simulate_history(
        stage_one,
        stage_two,
        mode=ContextMode.GENERIC,
        codex_root=root,
        pi_root=tmp_path / "missing",
        since=now - timedelta(hours=1),
        now=now,
        ttl_seconds=1_800,
        max_examples=1,
    )
    assert stage_one.prompts == []
    assert len(stage_two.prompts) == 1
    example = result["replay_examples"][0]
    assert example["public_activity"] is None
    assert example["generic_fallback"] is False
    assert "Approved candid activity" not in stage_two.prompts[0]


def test_counts_only_history_makes_no_model_calls(tmp_path) -> None:
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)
    root = tmp_path / "codex"
    codex_history(root, now)
    stage_one, stage_two = StageOne(), StageTwo()
    result = simulate_history(
        stage_one,
        stage_two,
        mode=ContextMode.CANDID,
        codex_root=root,
        pi_root=tmp_path / "missing",
        since=now - timedelta(hours=1),
        now=now,
        ttl_seconds=1_800,
        max_examples=0,
    )
    assert result["replay_examples"] == []
    assert stage_one.prompts == stage_two.prompts == []
