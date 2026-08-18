"""Foreground orchestration from exact events to optional public presence."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_away_message.config import Settings
from agent_away_message.generation import (
    ActivityRecord,
    CompletionClient,
    GenerationError,
    aggregate_fingerprint,
    generate_state,
    load_candid_statuses,
    load_history,
    safe_generation_error_code,
    save_history,
    valid_fallback,
)
from agent_away_message.models import (
    ActiveSession,
    ContextMode,
    GenerationResult,
    Presence,
    PublicationMode,
)
from agent_away_message.publisher import Publisher
from agent_away_message.store import EventStore


class PreviewGenerationError(RuntimeError):
    """A preview exposes a safe generation failure class instead of stale prose."""

    def __init__(self, error: GenerationError) -> None:
        self.reason = safe_generation_error_code(error)
        super().__init__(f"local model generation failed ({self.reason})")


def safe_generation_error(error: Exception) -> str:
    """Use one public diagnostic contract for preview and daemon failures."""
    if isinstance(error, PreviewGenerationError):
        return error.reason
    return safe_generation_error_code(error)


@dataclass(frozen=True, slots=True)
class _PublicSnapshot:
    count: int
    harnesses: frozenset[str]
    context: frozenset[str]


class CountChangeDebouncer:
    """Delay only count-only presentation churn while preserving exact state."""

    def __init__(
        self,
        *,
        choose_delay: Callable[[int, int], int] = random.randint,
        minimum_seconds: int = 60,
        maximum_seconds: int = 120,
    ) -> None:
        self._choose_delay = choose_delay
        self._minimum_seconds = minimum_seconds
        self._maximum_seconds = maximum_seconds
        self._stable: _PublicSnapshot | None = None
        self._pending_until: datetime | None = None

    def admit(
        self, sessions: list[ActiveSession], now: datetime
    ) -> list[ActiveSession] | None:
        """Return current exact sessions when admitted, or None while coalescing."""
        snapshot = _public_snapshot(sessions)
        if self._stable is None or snapshot.count == 0:
            return self._admit(sessions, snapshot)
        meaningful = (
            snapshot.harnesses != self._stable.harnesses
            or snapshot.context != self._stable.context
        )
        if meaningful:
            return self._admit(sessions, snapshot)
        if snapshot.count == self._stable.count:
            self._pending_until = None
            return sessions
        if self._pending_until is None:
            delay = self._choose_delay(self._minimum_seconds, self._maximum_seconds)
            self._pending_until = now + timedelta(seconds=delay)
        if now < self._pending_until:
            return None
        return self._admit(sessions, snapshot)

    def seconds_until_due(self, now: datetime) -> float | None:
        """Return the remaining presentation hold for poll-loop scheduling."""
        if self._pending_until is None:
            return None
        return max(0.0, (self._pending_until - now).total_seconds())

    def _admit(
        self, sessions: list[ActiveSession], snapshot: _PublicSnapshot
    ) -> list[ActiveSession]:
        self._stable = snapshot
        self._pending_until = None
        return sessions


def _public_snapshot(sessions: list[ActiveSession]) -> _PublicSnapshot:
    return _PublicSnapshot(
        count=len(sessions),
        harnesses=frozenset(item.harness.value for item in sessions),
        context=frozenset(
            phrase
            for item in sessions
            for phrase in (item.public_activity_hint, item.candid_status)
            if phrase
        ),
    )


def load_active_sessions(settings: Settings, now: datetime) -> list[ActiveSession]:
    """Load exact liveness and attach only separately validated display context."""
    sessions = EventStore(settings.events_path).active_sessions(now)
    if settings.context_mode is ContextMode.CANDID:
        activities = load_candid_statuses(
            settings.candid_path, settings.activity_window_limit
        )
        live_ids = {item.session_id for item in sessions}
        eligible: dict[str, ActivityRecord] = {}
        for key, activity in activities.items():
            session_id, _token = key.split(":", 1)
            if session_id in live_ids and activity.expires_at > now:
                previous = eligible.get(session_id)
                if previous is None or activity.observed_at > previous.observed_at:
                    eligible[session_id] = activity
        return [
            ActiveSession(
                item.harness,
                item.session_id,
                item.expires_at,
                item.public_activity_hint,
                item.current_turn_token,
                eligible[item.session_id].text if item.session_id in eligible else None,
            )
            for item in sessions
        ]
    return sessions


def refresh(
    settings: Settings,
    client: CompletionClient,
    publisher: Publisher,
    now: datetime | None = None,
    *,
    choose_cadence: Callable[[int, int], int] = random.randint,
    sessions: list[ActiveSession] | None = None,
    on_generation_error: Callable[[GenerationError], None] | None = None,
) -> Presence | None:
    """Refresh immediately on aggregate change and randomly while stable."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    sessions = (
        sessions if sessions is not None else load_active_sessions(settings, current)
    )
    if not sessions:
        publisher.publish(None)
        return None
    history = load_history(settings.history_path, settings.history_limit)
    fingerprint = aggregate_fingerprint(sessions, settings.context_mode)
    latest = history[-1] if history else None
    if (
        latest is not None
        and latest.aggregate_fingerprint == fingerprint
        and current < latest.refresh_after
    ):
        generated = latest
    else:
        generated = _generate_or_fallback(
            settings,
            client,
            sessions,
            current,
            history,
            fingerprint,
            choose_cadence,
            on_generation_error,
        )
    if generated is None:
        publisher.publish(None)
        return None
    count = len(sessions)
    noun = "agent" if count == 1 else "agents"
    presence = Presence(
        details=f"{count} coding {noun} active",
        state=generated.state,
    )
    publisher.publish(presence)
    return presence


def _generate_or_fallback(
    settings: Settings,
    client: CompletionClient,
    sessions: list[ActiveSession],
    current: datetime,
    history: list[GenerationResult],
    fingerprint: str,
    choose_cadence: Callable[[int, int], int],
    on_generation_error: Callable[[GenerationError], None] | None,
) -> GenerationResult | None:
    try:
        cadence = choose_cadence(
            settings.generation_cadence_min_seconds,
            settings.generation_cadence_max_seconds,
        )
        generated = generate_state(
            client,
            sessions,
            settings.context_mode,
            now=current,
            refresh_after=current + timedelta(seconds=cadence),
            fingerprint=fingerprint,
            recent_states=[item.state for item in history],
        )
    except GenerationError as error:
        if settings.publication_mode is PublicationMode.PREVIEW:
            raise PreviewGenerationError(error) from error
        if on_generation_error is not None:
            on_generation_error(error)
        return valid_fallback(history, current, settings.retain_last_seconds)
    save_history(settings.history_path, [*history, generated], settings.history_limit)
    return generated
