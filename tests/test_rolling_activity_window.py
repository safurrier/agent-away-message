from __future__ import annotations

import shutil
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_away_message.cli import _open_context_socket
from agent_away_message.config import Settings
from agent_away_message.daemon import load_active_sessions
from agent_away_message.generation import load_candid_statuses, save_candid_status
from agent_away_message.models import (
    ContextMode,
    Harness,
    LifecycleEvent,
    LifecycleRecord,
)
from agent_away_message.store import EventStore


def _record(session: str, event: LifecycleEvent, now: datetime) -> LifecycleRecord:
    return LifecycleRecord(
        1, Harness.PI, session, event, now, now + timedelta(minutes=30), "test"
    )


def test_settled_activity_supersedes_provisional_for_same_turn(tmp_path) -> None:
    path = tmp_path / "activity-window.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    save_candid_status(
        path,
        "a" * 32,
        "b" * 32,
        "reviewing an integration",
        phase="provisional",
        observed_at=now,
    )
    save_candid_status(
        path,
        "a" * 32,
        "b" * 32,
        "updating the adapter",
        phase="settled",
        observed_at=now + timedelta(seconds=1),
    )
    activities = load_candid_statuses(path)
    assert len(activities) == 1
    activity = activities["a" * 32 + ":" + "b" * 32]
    assert activity.phase == "settled"
    assert activity.text == "updating the adapter"
    # A late datagram must not turn a completed activity back into provisional.
    save_candid_status(
        path,
        "a" * 32,
        "b" * 32,
        "reviewing an integration",
        phase="provisional",
        observed_at=now,
    )
    assert (
        load_candid_statuses(path)["a" * 32 + ":" + "b" * 32].text
        == "updating the adapter"
    )


def test_activity_requires_live_unexpired_source_but_count_is_exact(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path, context_mode=ContextMode.CANDID)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = EventStore(settings.events_path)
    store.append(_record("a" * 32, LifecycleEvent.PI_INPUT, now))
    store.append(_record("b" * 32, LifecycleEvent.PI_INPUT, now))
    save_candid_status(
        settings.candid_path,
        "a" * 32,
        "c" * 32,
        "reviewing an integration",
        observed_at=now,
    )
    save_candid_status(
        settings.candid_path,
        "b" * 32,
        "d" * 32,
        "updating the adapter",
        observed_at=now - timedelta(minutes=31),
        expires_at=now - timedelta(minutes=1),
    )
    active = load_active_sessions(settings, now)
    assert len(active) == 2
    assert {item.candid_status for item in active} == {"reviewing an integration", None}
    store.append(
        _record(
            "a" * 32, LifecycleEvent.PI_SESSION_SHUTDOWN, now + timedelta(seconds=1)
        )
    )
    active = load_active_sessions(settings, now + timedelta(seconds=2))
    assert len(active) == 1
    assert active[0].candid_status is None


def _short_socket_settings() -> Settings:
    return Settings(
        state_dir=Path(tempfile.mkdtemp(dir="/tmp", prefix="aam-socket-")),
        context_mode=ContextMode.CANDID,
    )


def _remove_socket_state(settings: Settings) -> None:
    settings.context_socket_path.unlink(missing_ok=True)
    settings.context_socket_path.with_suffix(".sock.lock").unlink(missing_ok=True)
    shutil.rmtree(settings.state_dir)


def test_concurrent_socket_starters_admit_one_owner(tmp_path) -> None:
    settings = _short_socket_settings()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_open_context_socket, settings) for _ in range(2)]
    owners = [future.result() for future in futures if future.exception() is None]
    assert len(owners) == 1
    assert sum(future.exception() is not None for future in futures) == 1
    try:
        assert settings.context_socket_path.exists()
        assert (
            settings.context_socket_path.with_suffix(".sock.lock").stat().st_mode
            & 0o777
            == 0o600
        )
    finally:
        owners[0].close()
        _remove_socket_state(settings)


def test_socket_recovers_after_crash_leaves_stale_socket_and_lock_artifact(
    tmp_path,
) -> None:
    settings = _short_socket_settings()
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    stale.bind(str(settings.context_socket_path))
    stale.close()  # Simulate process death: pathname and unlocked lock artifact remain.
    lock_path = settings.context_socket_path.with_suffix(".sock.lock")
    lock_path.touch(mode=0o600)

    owner = _open_context_socket(settings)
    try:
        assert settings.context_socket_path.exists()
        with pytest.raises(OSError, match="already owned"):
            _open_context_socket(settings)
    finally:
        owner.close()
    # Lock artifacts are intentionally durable but ownership was released.
    successor = _open_context_socket(settings)
    successor.close()
    _remove_socket_state(settings)


def test_socket_teardown_cannot_unlink_a_successor_socket(tmp_path) -> None:
    settings = _short_socket_settings()
    first = _open_context_socket(settings)
    with pytest.raises(OSError, match="already owned"):
        _open_context_socket(settings)
    first.close()
    successor = _open_context_socket(settings)
    try:
        assert settings.context_socket_path.exists()
        first.close()  # Idempotent old teardown cannot touch the successor.
        assert settings.context_socket_path.exists()
    finally:
        successor.close()
        _remove_socket_state(settings)
