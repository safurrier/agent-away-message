"""Typed, privacy-safe domain contracts."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

SCHEMA_VERSION: Final = 1


class Harness(StrEnum):
    CODEX = "codex"
    PI = "pi"


class LifecycleEvent(StrEnum):
    STARTED = "started"
    STOPPED = "stopped"
    CODEX_USER_PROMPT_SUBMIT = "codex.user_prompt_submit"
    CODEX_PRE_TOOL_USE = "codex.pre_tool_use"
    CODEX_POST_TOOL_USE = "codex.post_tool_use"
    CODEX_STOP = "codex.stop"
    PI_SESSION_START = "pi.session_start"
    PI_INPUT = "pi.input"
    PI_AGENT_START = "pi.agent_start"
    PI_TOOL_CALL = "pi.tool_call"
    PI_AGENT_END = "pi.agent_end"
    PI_SESSION_SHUTDOWN = "pi.session_shutdown"


TURN_EVENTS: Final = {
    LifecycleEvent.CODEX_USER_PROMPT_SUBMIT,
    LifecycleEvent.PI_INPUT,
}


class ContextMode(StrEnum):
    """The two supported public-status context policies."""

    GENERIC = "generic"
    CANDID = "candid"


class PublicationMode(StrEnum):
    PREVIEW = "preview"
    DISCORD = "discord"


def turn_token_for(
    harness: Harness, session_id: str, event: LifecycleEvent, occurred_at: datetime
) -> str:
    """Derive an opaque exact-turn token from an already allowlisted event record."""
    material = "\0".join(
        (
            harness.value,
            session_id,
            event.value,
            occurred_at.astimezone(UTC).isoformat(),
        )
    )
    return hashlib.blake2s(material.encode(), digest_size=16).hexdigest()


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    schema_version: int
    harness: Harness
    session_id: str
    event: LifecycleEvent
    occurred_at: datetime
    expires_at: datetime
    provenance: str
    public_activity_hint: str | None = None

    def to_json(self) -> dict[str, str | int | None]:
        payload = asdict(self)
        payload["harness"] = self.harness.value
        payload["event"] = self.event.value
        payload["occurred_at"] = self.occurred_at.astimezone(UTC).isoformat()
        payload["expires_at"] = self.expires_at.astimezone(UTC).isoformat()
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> LifecycleRecord:
        schema_value = payload["schema_version"]
        if not isinstance(schema_value, (int, str)):
            raise ValueError("schema version must be an integer")
        return cls(
            schema_version=int(schema_value),
            harness=Harness(str(payload["harness"])),
            session_id=str(payload["session_id"]),
            event=LifecycleEvent(str(payload["event"])),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            provenance=str(payload["provenance"]),
            public_activity_hint=str(payload["public_activity_hint"])
            if payload.get("public_activity_hint") is not None
            else None,
        )


@dataclass(frozen=True, slots=True)
class ActiveSession:
    harness: Harness
    session_id: str
    expires_at: datetime
    public_activity_hint: str | None
    current_turn_token: str | None = None
    candid_status: str | None = None


@dataclass(frozen=True, slots=True)
class Presence:
    details: str
    state: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    state: str
    generated_at: datetime
    refresh_after: datetime
    aggregate_fingerprint: str
