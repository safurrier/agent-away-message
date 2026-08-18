"""Append-only local event storage and deterministic liveness reduction."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from agent_away_message.models import (
    TURN_EVENTS,
    ActiveSession,
    LifecycleEvent,
    LifecycleRecord,
    turn_token_for,
)
from agent_away_message.privacy import PrivacyError, validate_public_hint

_ID: Final = re.compile(r"^[a-f0-9]{32}$")
_PROVENANCE: Final = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_COMPLETION_EVENTS: Final = {
    LifecycleEvent.CODEX_STOP,
    LifecycleEvent.PI_AGENT_END,
}
_PAIRING_RESET_EVENTS: Final = {
    LifecycleEvent.STARTED,
    LifecycleEvent.STOPPED,
    LifecycleEvent.PI_SESSION_START,
    LifecycleEvent.PI_SESSION_SHUTDOWN,
}


class EventStoreError(ValueError):
    """Raised for malformed or unsafe event records."""


class EventStore:
    """A JSONL store where each append is one atomic write on local POSIX filesystems."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: LifecycleRecord) -> None:
        """Persist an allowlisted record without storing source payloads."""
        self._validate(record)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        encoded = (json.dumps(record.to_json(), separators=(",", ":")) + "\n").encode()
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise EventStoreError("event append was incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def completion_turn_token(self, completion: LifecycleRecord) -> str | None:
        """Pair a completion with the oldest unmatched persisted input turn.

        The caller appends ``completion`` before using this reducer, so the
        completion itself participates in the evidence. Pairing follows append
        order rather than timestamps: hook delivery can be reordered, while the
        persisted sequence is the exact local observation order.
        """
        if completion.event not in _COMPLETION_EVENTS:
            return None

        unmatched: dict[tuple[str, str], list[str]] = {}
        paired_token: str | None = None
        for record in self._read_records():
            key = (record.harness.value, record.session_id)
            pending = unmatched.setdefault(key, [])
            if record.event in _PAIRING_RESET_EVENTS:
                pending.clear()
            elif record.event in TURN_EVENTS:
                pending.append(
                    turn_token_for(
                        record.harness,
                        record.session_id,
                        record.event,
                        record.occurred_at,
                    )
                )
            elif record.event in _COMPLETION_EVENTS:
                token = pending.pop(0) if pending else None
                if record == completion:
                    paired_token = token
        return paired_token

    def inspect_fixture(self) -> int:
        """Reject fixture fields that could contain forbidden source content."""
        if not self.path.exists():
            raise EventStoreError("fixture does not exist")
        allowed = {
            "schema_version",
            "harness",
            "session_id",
            "event",
            "occurred_at",
            "expires_at",
            "provenance",
            "public_activity_hint",
        }
        records = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict) or set(payload) - allowed:
                    raise EventStoreError("fixture contains a forbidden field")
                self._validate(LifecycleRecord.from_json(payload))
            except (
                KeyError,
                PrivacyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise EventStoreError("fixture contains an invalid record") from error
            records += 1
        return records

    def active_sessions(self, now: datetime | None = None) -> list[ActiveSession]:
        """Reduce exact events while retaining the current-turn version across heartbeats."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        latest: dict[
            tuple[str, str], tuple[LifecycleRecord, str | None, str | None]
        ] = {}
        reset_hint_events = {
            LifecycleEvent.STARTED,
            LifecycleEvent.CODEX_USER_PROMPT_SUBMIT,
            LifecycleEvent.PI_SESSION_START,
            LifecycleEvent.PI_INPUT,
        }
        reset_turn_events = {
            LifecycleEvent.STARTED,
            LifecycleEvent.STOPPED,
            LifecycleEvent.PI_SESSION_START,
            LifecycleEvent.PI_SESSION_SHUTDOWN,
        }
        for record in sorted(self._read_records(), key=lambda item: item.occurred_at):
            key = (record.harness.value, record.session_id)
            prior_hint = latest[key][1] if key in latest else None
            prior_turn = latest[key][2] if key in latest else None
            hint = (
                record.public_activity_hint
                if record.event in reset_hint_events
                else (record.public_activity_hint or prior_hint)
            )
            turn_token = (
                turn_token_for(
                    record.harness,
                    record.session_id,
                    record.event,
                    record.occurred_at,
                )
                if record.event in TURN_EVENTS
                else (None if record.event in reset_turn_events else prior_turn)
            )
            latest[key] = (record, hint, turn_token)
        sessions: list[ActiveSession] = []
        for record, hint, turn_token in latest.values():
            if (
                record.event
                in {
                    LifecycleEvent.STOPPED,
                    LifecycleEvent.PI_SESSION_SHUTDOWN,
                }
                or record.expires_at <= current
            ):
                continue
            sessions.append(
                ActiveSession(
                    harness=record.harness,
                    session_id=record.session_id,
                    expires_at=record.expires_at,
                    public_activity_hint=hint,
                    current_turn_token=turn_token,
                )
            )
        return sorted(sessions, key=lambda item: (item.harness.value, item.session_id))

    def _read_records(self) -> list[LifecycleRecord]:
        if not self.path.exists():
            return []
        records: list[LifecycleRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise EventStoreError("event record must be an object")
                record = LifecycleRecord.from_json(payload)
                self._validate(record)
            except (
                EventStoreError,
                KeyError,
                PrivacyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise EventStoreError(
                    "event store contains an invalid record"
                ) from error
            records.append(record)
        return records

    @staticmethod
    def _validate(record: LifecycleRecord) -> None:
        if record.schema_version != 1:
            raise EventStoreError("unsupported event schema version")
        if not _ID.fullmatch(record.session_id):
            raise EventStoreError("session identity must be a short opaque identifier")
        if not _PROVENANCE.fullmatch(record.provenance):
            raise EventStoreError("provenance must be a short allowlisted identifier")
        if record.occurred_at.tzinfo is None or record.expires_at.tzinfo is None:
            raise EventStoreError("event times must include a timezone")
        if record.public_activity_hint is not None:
            try:
                validate_public_hint(record.public_activity_hint)
            except PrivacyError as error:
                raise EventStoreError("public activity hint is not safe") from error
