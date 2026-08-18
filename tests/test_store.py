from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_away_message.models import (
    Harness,
    LifecycleEvent,
    LifecycleRecord,
    turn_token_for,
)
from agent_away_message.store import EventStore, EventStoreError


def record(
    event: LifecycleEvent,
    now: datetime,
    session_id: str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
) -> LifecycleRecord:
    return LifecycleRecord(
        schema_version=1,
        harness=Harness.CODEX,
        session_id=session_id,
        event=event,
        occurred_at=now,
        expires_at=now + timedelta(minutes=5),
        provenance="test",
    )


def test_event_store_reduces_stopped_and_stale_sessions(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = EventStore(tmp_path / "events.jsonl")
    store.append(
        record(LifecycleEvent.STARTED, now, "11111111111111111111111111111111")
    )
    store.append(
        record(
            LifecycleEvent.STOPPED,
            now + timedelta(seconds=1),
            "22222222222222222222222222222222",
        )
    )
    stale = record(LifecycleEvent.STARTED, now, "33333333333333333333333333333333")
    store.append(
        LifecycleRecord(
            schema_version=stale.schema_version,
            harness=stale.harness,
            session_id=stale.session_id,
            event=stale.event,
            occurred_at=stale.occurred_at,
            expires_at=now - timedelta(seconds=1),
            provenance=stale.provenance,
        )
    )

    assert [item.session_id for item in store.active_sessions(now)] == [
        "11111111111111111111111111111111"
    ]


def test_event_store_rejects_nonpublic_hint(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    unsafe = LifecycleRecord(
        schema_version=1,
        harness=Harness.PI,
        session_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        event=LifecycleEvent.STARTED,
        occurred_at=now,
        expires_at=now + timedelta(minutes=5),
        provenance="test",
        public_activity_hint="edit /private/repository",
    )

    with pytest.raises(EventStoreError):
        EventStore(tmp_path / "events.jsonl").append(unsafe)


def test_heartbeat_preserves_current_public_hint(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = EventStore(tmp_path / "events.jsonl")
    store.append(
        LifecycleRecord(
            1,
            Harness.CODEX,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            LifecycleEvent.CODEX_USER_PROMPT_SUBMIT,
            now,
            now + timedelta(minutes=5),
            "test",
            "battling the build",
        )
    )
    store.append(
        LifecycleRecord(
            1,
            Harness.CODEX,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            LifecycleEvent.CODEX_POST_TOOL_USE,
            now + timedelta(seconds=1),
            now + timedelta(minutes=5),
            "test",
        )
    )

    assert store.active_sessions(now)[0].public_activity_hint == "battling the build"


def test_event_store_derives_current_turn_from_pre_token_records(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = tmp_path / "events.jsonl"
    prompt = record(LifecycleEvent.CODEX_USER_PROMPT_SUBMIT, now)
    path.write_text(json.dumps(prompt.to_json()) + "\n", encoding="utf-8")
    store = EventStore(path)
    store.append(record(LifecycleEvent.CODEX_POST_TOOL_USE, now + timedelta(seconds=1)))
    persisted = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]

    assert all("turn_token" not in payload for payload in persisted)
    assert store.active_sessions(now)[0].current_turn_token == turn_token_for(
        Harness.CODEX,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        LifecycleEvent.CODEX_USER_PROMPT_SUBMIT,
        now,
    )


@pytest.mark.parametrize(
    ("harness", "input_event", "completion_event", "reset_event"),
    [
        (
            Harness.CODEX,
            LifecycleEvent.CODEX_USER_PROMPT_SUBMIT,
            LifecycleEvent.CODEX_STOP,
            LifecycleEvent.STOPPED,
        ),
        (
            Harness.PI,
            LifecycleEvent.PI_INPUT,
            LifecycleEvent.PI_AGENT_END,
            LifecycleEvent.PI_SESSION_SHUTDOWN,
        ),
    ],
)
def test_completion_pairing_uses_oldest_unmatched_turn_and_clears_on_reset(
    tmp_path, harness, input_event, completion_event, reset_event
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = EventStore(tmp_path / "events.jsonl")
    session_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def append(event: LifecycleEvent, offset: int) -> LifecycleRecord:
        occurred_at = now + timedelta(seconds=offset)
        item = LifecycleRecord(
            1,
            harness,
            session_id,
            event,
            occurred_at,
            occurred_at + timedelta(minutes=5),
            "test",
        )
        store.append(item)
        return item

    first = append(input_event, 0)
    second = append(input_event, 1)
    first_completion = append(completion_event, 2)
    assert store.completion_turn_token(first_completion) == turn_token_for(
        harness, session_id, input_event, first.occurred_at
    )
    second_completion = append(completion_event, 3)
    assert store.completion_turn_token(second_completion) == turn_token_for(
        harness, session_id, input_event, second.occurred_at
    )

    append(reset_event, 4)
    unmatched_completion = append(completion_event, 5)
    assert store.completion_turn_token(unmatched_completion) is None


def test_pi_session_restart_clears_prior_turn_token(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = EventStore(tmp_path / "events.jsonl")
    session_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    for offset, event in enumerate(
        (
            LifecycleEvent.PI_INPUT,
            LifecycleEvent.PI_SESSION_SHUTDOWN,
            LifecycleEvent.PI_SESSION_START,
        )
    ):
        occurred_at = now + timedelta(seconds=offset)
        store.append(
            LifecycleRecord(
                1,
                Harness.PI,
                session_id,
                event,
                occurred_at,
                occurred_at + timedelta(minutes=5),
                "test",
            )
        )

    assert (
        store.active_sessions(now + timedelta(seconds=2))[0].current_turn_token is None
    )


def test_event_store_reports_malformed_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("not json\n", encoding="utf-8")

    with pytest.raises(EventStoreError):
        EventStore(path).active_sessions()
