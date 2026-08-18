from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_away_message.daemon import CountChangeDebouncer
from agent_away_message.models import ActiveSession, Harness

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def sessions(count: int, *, hint: str | None = None) -> list[ActiveSession]:
    return [
        ActiveSession(
            Harness.CODEX,
            str(index),
            NOW + timedelta(hours=1),
            None,
            candid_status=hint,
        )
        for index in range(count)
    ]


def test_transient_count_change_is_cancelled_without_admission() -> None:
    debouncer = CountChangeDebouncer(choose_delay=lambda _low, _high: 90)

    assert debouncer.admit(sessions(5), NOW) is not None
    assert debouncer.admit(sessions(18), NOW + timedelta(seconds=1)) is None
    restored = debouncer.admit(sessions(5), NOW + timedelta(seconds=30))

    assert restored is not None
    assert len(restored) == 5


def test_settled_large_count_is_admitted_exactly() -> None:
    debouncer = CountChangeDebouncer(choose_delay=lambda _low, _high: 90)
    debouncer.admit(sessions(5), NOW)
    debouncer.admit(sessions(18), NOW + timedelta(seconds=1))

    admitted = debouncer.admit(sessions(18), NOW + timedelta(seconds=91))

    assert admitted is not None
    assert len(admitted) == 18


def test_pending_deadline_can_cap_the_daemon_poll_sleep() -> None:
    debouncer = CountChangeDebouncer(choose_delay=lambda _low, _high: 90)
    debouncer.admit(sessions(5), NOW)
    debouncer.admit(sessions(18), NOW + timedelta(seconds=1))

    assert debouncer.seconds_until_due(NOW + timedelta(seconds=31)) == 60


def test_context_change_and_zero_agents_bypass_count_debounce() -> None:
    debouncer = CountChangeDebouncer(choose_delay=lambda _low, _high: 90)
    debouncer.admit(sessions(5), NOW)

    contextual = debouncer.admit(
        sessions(18, hint="reviewing an integration"), NOW + timedelta(seconds=1)
    )
    stopped = debouncer.admit([], NOW + timedelta(seconds=2))

    assert contextual is not None and len(contextual) == 18
    assert stopped == []
