from __future__ import annotations

import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime

from agent_away_message.config import Settings
from agent_away_message.daemon import refresh
from agent_away_message.models import (
    Harness,
    LifecycleEvent,
    LifecycleRecord,
    PublicationMode,
)
from agent_away_message.publisher import MemoryPublisher
from agent_away_message.store import EventStore


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return '{"state":"battling the afternoon slump"}'


def test_synthetic_event_reaches_preview_publisher(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    settings = Settings(state_dir=tmp_path, publication_mode=PublicationMode.PREVIEW)
    EventStore(settings.events_path).append(
        LifecycleRecord(
            schema_version=1,
            harness=Harness.CODEX,
            session_id="33333333333333333333333333333333",
            event=LifecycleEvent.STARTED,
            occurred_at=now,
            expires_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            provenance="fixture",
        )
    )
    publisher = MemoryPublisher()
    model = FakeModel()

    presence = refresh(settings, model, publisher, now)
    repeated = refresh(settings, model, publisher, now)

    assert model.calls == 1
    assert repeated is not None

    assert presence == publisher.last
    assert presence is not None
    assert presence.details == "1 coding agent active"


def test_setup_generated_codex_hook_reaches_status_across_processes(tmp_path) -> None:
    state_dir = tmp_path / "state with spaces"
    hooks_path = tmp_path / "hooks.json"
    module_command = [sys.executable, "-m", "agent_away_message"]
    setup = subprocess.run(  # noqa: S603
        [
            *module_command,
            "--state-dir",
            str(state_dir),
            "--json",
            "setup",
            "--codex-config",
            str(hooks_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert setup.returncode == 0, setup.stderr
    hook_command = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"][
        "UserPromptSubmit"
    ][0]["hooks"][0]["command"]

    hook = subprocess.run(  # noqa: S603
        shlex.split(hook_command),
        input='{"hook_event_name":"UserPromptSubmit","session_id":"e2e-session"}',
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(  # noqa: S603
        [*module_command, "--state-dir", str(state_dir), "--json", "status"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert hook.returncode == 0
    assert hook.stdout == hook.stderr == ""
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["active_agents"] == 1
