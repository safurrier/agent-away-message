"""Fail-soft native hook adapters retaining only allowlisted lifecycle facts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from platformdirs import user_state_path

from agent_away_message.identity import IdentityError, digest_session_id
from agent_away_message.models import (
    SCHEMA_VERSION,
    Harness,
    LifecycleEvent,
    LifecycleRecord,
)
from agent_away_message.privacy import PrivacyError, validate_public_hint

_CODEX_EVENT_MAP: Final = {
    "UserPromptSubmit": LifecycleEvent.CODEX_USER_PROMPT_SUBMIT,
    "PreToolUse": LifecycleEvent.CODEX_PRE_TOOL_USE,
    "PostToolUse": LifecycleEvent.CODEX_POST_TOOL_USE,
    "Stop": LifecycleEvent.CODEX_STOP,
}
_PI_EVENT_MAP: Final = {
    "session_start": LifecycleEvent.PI_SESSION_START,
    "input": LifecycleEvent.PI_INPUT,
    "agent_start": LifecycleEvent.PI_AGENT_START,
    "tool_call": LifecycleEvent.PI_TOOL_CALL,
    "agent_end": LifecycleEvent.PI_AGENT_END,
    "session_shutdown": LifecycleEvent.PI_SESSION_SHUTDOWN,
}
MAX_CANDID_CONTEXT_CHARS: Final = 6_000


class AdapterError(ValueError):
    """Raised when a hook payload lacks the minimal native contract."""


def opaque_session_id(value: object, identity_key: Path | None) -> str:
    try:
        return digest_session_id(
            value,
            identity_key or user_state_path("agent-away-message") / "identity.key",
        )
    except IdentityError as error:
        raise AdapterError("hook input requires a session identity") from error


def codex_record(
    payload: str,
    ttl_seconds: int,
    *,
    identity_key: Path | None = None,
    fallback_session_id: str | int | None = None,
) -> LifecycleRecord:
    parsed = _parse(payload)
    event_name = parsed.get("hook_event_name")
    if not isinstance(event_name, str) or event_name not in _CODEX_EVENT_MAP:
        raise AdapterError("unsupported native lifecycle event")
    return _record(
        Harness.CODEX,
        opaque_session_id(
            parsed.get("session_id", fallback_session_id),
            identity_key,
        ),
        _CODEX_EVENT_MAP[event_name],
        ttl_seconds,
        _optional_hint(parsed),
    )


def pi_record(
    payload: str, ttl_seconds: int, *, identity_key: Path | None = None
) -> LifecycleRecord:
    parsed = _parse(payload)
    event_name = parsed.get("event")
    if not isinstance(event_name, str) or event_name not in _PI_EVENT_MAP:
        raise AdapterError("unsupported native lifecycle event")
    return _record(
        Harness.PI,
        opaque_session_id(parsed.get("session_id"), identity_key),
        _PI_EVENT_MAP[event_name],
        ttl_seconds,
        _optional_hint(parsed),
    )


def codex_context_source(payload: str) -> str | None:
    """Return Codex current-turn text only to an ephemeral caller, never a record."""
    return _context_source(payload, "prompt")


def pi_context_source(payload: str) -> str | None:
    """Return Pi input text only to an ephemeral caller, never a record."""
    return _context_source(payload, "text")


def codex_candid_source(payload: str) -> str | None:
    """Return only Codex's native final assistant text for a Stop event."""
    parsed = _parse(payload)
    if parsed.get("hook_event_name") != "Stop":
        return None
    return _bounded_source(parsed.get("last_assistant_message"))


def pi_candid_source(payload: str) -> str | None:
    """Return the Pi extension's bounded current-turn context at agent_end."""
    parsed = _parse(payload)
    if parsed.get("event") != "agent_end":
        return None
    return _bounded_source(parsed.get("candid_context"))


def _context_source(payload: str, field: str) -> str | None:
    parsed = _parse(payload)
    source = parsed.get(field)
    return source if isinstance(source, str) and source else None


def _bounded_source(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and 0 < len(value) <= MAX_CANDID_CONTEXT_CHARS
        else None
    )


def _parse(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AdapterError("hook input must be JSON") from error
    if not isinstance(payload, dict):
        raise AdapterError("hook input must be an object")
    return payload


def _optional_hint(payload: dict[str, object]) -> str | None:
    value = payload.get("public_activity_hint")
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdapterError("public activity hint must be text")
    try:
        return validate_public_hint(value)
    except PrivacyError as error:
        raise AdapterError("public activity hint is not safe") from error


def _record(
    harness: Harness,
    session_id: str,
    event: LifecycleEvent,
    ttl_seconds: int,
    hint: str | None,
) -> LifecycleRecord:
    now = datetime.now(UTC)
    return LifecycleRecord(
        SCHEMA_VERSION,
        harness,
        session_id,
        event,
        now,
        now + timedelta(seconds=ttl_seconds),
        f"{harness.value}-hook",
        hint,
    )
