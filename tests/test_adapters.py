from __future__ import annotations

import json

from agent_away_message.adapters import (
    codex_candid_source,
    codex_record,
    pi_candid_source,
    pi_record,
)
from agent_away_message.models import Harness, LifecycleEvent


def test_codex_adapter_extracts_only_allowlisted_lifecycle_fields() -> None:
    record = codex_record(
        json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "agent_1",
                "prompt": "private source text",
                "command": "private command",
            }
        ),
        60,
    )

    stored = record.to_json()
    assert record.harness is Harness.CODEX
    assert record.event is LifecycleEvent.CODEX_USER_PROMPT_SUBMIT
    assert "prompt" not in stored
    assert "command" not in stored


def test_pi_adapter_maps_exact_extension_events() -> None:
    record = pi_record('{"event":"agent_end","session_id":"agent_2"}', 60)

    assert record.harness is Harness.PI
    assert record.event is LifecycleEvent.PI_AGENT_END


def test_candid_sources_accept_only_native_completion_fields() -> None:
    assert (
        codex_candid_source(
            json.dumps(
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "Checked the adapter and its tests.",
                    "prompt": "must not be concatenated at Stop",
                }
            )
        )
        == "Checked the adapter and its tests."
    )
    assert (
        pi_candid_source(
            json.dumps(
                {
                    "event": "agent_end",
                    "candid_context": "User: check it\nAssistant: checked it",
                    "text": "ignored input field",
                }
            )
        )
        == "User: check it\nAssistant: checked it"
    )


def test_candid_sources_ignore_wrong_events_and_oversized_text() -> None:
    assert (
        codex_candid_source(
            '{"hook_event_name":"PreToolUse","last_assistant_message":"x"}'
        )
        is None
    )
    assert pi_candid_source('{"event":"input","candid_context":"x"}') is None
    assert (
        codex_candid_source(
            json.dumps(
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "x" * 6_001,
                }
            )
        )
        is None
    )
