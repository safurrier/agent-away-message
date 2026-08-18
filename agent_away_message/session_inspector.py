"""Structural dogfood inspection that never emits or persists session content."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Final

from agent_away_message.models import Harness

_TYPES: Final = {
    Harness.CODEX: {
        "session_meta",
        "response_item",
        "event_msg",
        "turn_context",
        "compacted",
    },
    Harness.PI: {
        "session",
        "message",
        "model_change",
        "thinking_level_change",
        "compaction",
        "branch_summary",
        "custom",
        "custom_message",
        "label",
        "session_info",
    },
}


class SessionInspectionError(ValueError):
    """Raised when an explicitly supplied session is not structurally recognized."""


def inspect_session(path: Path, harness: Harness) -> dict[str, int]:
    """Return top-level record-type counts and discard all other parsed values."""
    counts: Counter[str] = Counter()
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise SessionInspectionError("session record must be an object")
                record_type = payload.get("type")
                if (
                    not isinstance(record_type, str)
                    or record_type not in _TYPES[harness]
                ):
                    raise SessionInspectionError(
                        "session record type is not allowlisted"
                    )
                counts[record_type] += 1
    except (OSError, json.JSONDecodeError) as error:
        raise SessionInspectionError("session is not readable JSONL") from error
    return dict(sorted(counts.items()))
