from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_away_message.generation import (
    CandidateRejectedError,
    GenerationError,
    ModelRequestError,
    build_prompt,
    generate_state,
    judge_public_activity,
    load_candid_statuses,
    quarantine_invalid_activity,
    quarantine_invalid_history,
    reduce_and_check_candid_activity,
    save_candid_status,
    save_history,
)
from agent_away_message.models import (
    ActiveSession,
    ContextMode,
    GenerationResult,
    Harness,
)
from agent_away_message.privacy import validate_message


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def session(*, candid: str | None = None) -> ActiveSession:
    return ActiveSession(
        harness=Harness.PI,
        session_id="session_1",
        expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        public_activity_hint="waiting for a reply",
        candid_status=candid,
    )


def test_generic_generation_uses_exactly_one_call_and_ignores_manual_task_hint() -> (
    None
):
    client = FakeClient(['{"state":"a few agents have formed a committee"}'])
    now = datetime(2026, 1, 1, tzinfo=UTC)

    result = generate_state(
        client,
        [session()],
        ContextMode.GENERIC,
        now=now,
        refresh_after=now,
        fingerprint="a" * 64,
    )

    assert result.state == "a few agents have formed a committee"
    assert len(client.prompts) == 1
    assert "waiting for a reply" not in client.prompts[0]
    assert "AIM-style activity status" in client.prompts[0]
    assert "Never imply the user is away or returning later" in client.prompts[0]
    assert "do not invent a task" in client.prompts[0]
    assert "never about the user, agents, or harnesses" in client.prompts[0]
    assert "one short, natural" in client.prompts[0]
    assert "describe only plurality or load" in client.prompts[0]
    assert "keep its broad task recognizable" in client.prompts[0]


def test_generic_malformed_or_unsafe_reply_has_no_repair_call() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for response in ("not json", '{"state":"open /private/repo"}'):
        client = FakeClient([response])
        with pytest.raises(CandidateRejectedError):
            generate_state(
                client,
                [session()],
                ContextMode.GENERIC,
                now=now,
                refresh_after=now,
                fingerprint="a" * 64,
            )
        assert len(client.prompts) == 1


def test_candid_normal_path_is_two_stage_one_calls_then_one_writer_call() -> None:
    raw = "raw-context-canary about adapter installation"
    stage_one = FakeClient(
        [
            '{"text":"updating an adapter and checking installation"}',
            '{"pass":true,"reason":null}',
        ]
    )
    activity = reduce_and_check_candid_activity(stage_one, raw)
    assert activity == "updating an adapter and checking installation"
    assert len(stage_one.prompts) == 2
    assert raw in stage_one.prompts[0]
    assert raw not in stage_one.prompts[1]
    assert activity in stage_one.prompts[1]
    assert "factually supported" in stage_one.prompts[1]

    stage_two = FakeClient(
        ['{"state":"the adapter remains unconvinced by installation"}']
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = generate_state(
        stage_two,
        [session(candid=activity)],
        ContextMode.CANDID,
        now=now,
        refresh_after=now,
        fingerprint="b" * 64,
    )
    assert result.state == "the adapter remains unconvinced by installation"
    assert len(stage_two.prompts) == 1
    assert activity in stage_two.prompts[0]
    assert raw not in stage_two.prompts[0]


def test_candidate_only_privacy_judge_rejects_recognizable_activity() -> None:
    client = FakeClient(['{"pass":false,"reason":"recognizable_or_too_specific"}'])
    assert not judge_public_activity(client, "debugging a named billing repository")
    assert len(client.prompts) == 1


def test_candid_reducer_abstention_or_privacy_rejection_returns_none() -> None:
    abstain = FakeClient(['{"text":null}'])
    assert reduce_and_check_candid_activity(abstain, "ambiguous source") is None
    assert len(abstain.prompts) == 1

    rejected = FakeClient(
        [
            '{"text":"debugging a named billing repository"}',
            '{"pass":false,"reason":"recognizable_or_too_specific"}',
        ]
    )
    assert reduce_and_check_candid_activity(rejected, "private source") is None
    assert len(rejected.prompts) == 2


def test_candid_structural_rejection_abstains_before_privacy_judge() -> None:
    client = FakeClient(['{"text":"debugging VenusBilling integration failures"}'])
    assert (
        reduce_and_check_candid_activity(client, "Debug VenusBilling failures") is None
    )
    assert len(client.prompts) == 1


class RequestFailingClient:
    def complete(self, _prompt: str) -> str:
        raise ModelRequestError("local model request failed")


def test_candid_transport_failure_remains_diagnosable() -> None:
    with pytest.raises(ModelRequestError):
        reduce_and_check_candid_activity(RequestFailingClient(), "source")


def test_stage_two_prompt_uses_recent_states_only_as_style_history() -> None:
    prompt = build_prompt(
        [session(candid="checking model embedding requirements")],
        ContextMode.CANDID,
        ["a few loose ends have formed a committee"],
    )
    assert "Approved candid activity" in prompt
    assert "checking model embedding requirements" in prompt
    assert "negative style examples only" in prompt
    assert "central image, notable vocabulary, opening grammar" in prompt
    assert "sentence skeleton" in prompt
    assert "plainly different" in prompt
    assert "three or more" in prompt
    assert "treat it as saturated" in prompt
    assert "truthful paraphrase or another facet" in prompt
    assert "never sacrifice task grounding" in prompt
    assert "returning later" in prompt


def test_recent_duplicate_fails_without_repair() -> None:
    client = FakeClient(['{"state":"checking recent changes"}'])
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(CandidateRejectedError) as rejected:
        generate_state(
            client,
            [session()],
            ContextMode.GENERIC,
            now=now,
            refresh_after=now,
            fingerprint="a" * 64,
            recent_states=["checking recent changes"],
        )
    assert rejected.value.code == "recent_duplicate"
    assert len(client.prompts) == 1


@pytest.mark.parametrize(
    "state",
    ["production system is down", "finished the usual knots", "taking a short break"],
)
def test_semantic_phrases_remain_structurally_valid(state: str) -> None:
    assert validate_message(state) == state


def test_invalid_public_history_is_quarantined_without_weakening_validation(
    tmp_path,
) -> None:
    path = tmp_path / "message-history.json"
    path.write_text('[{"state":"taking a short break"}]', encoding="utf-8")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    quarantined = quarantine_invalid_history(path, 8, now)

    assert (
        quarantined == tmp_path / "message-history.invalid-20260101T000000000000Z.json"
    )
    assert not path.exists()
    assert (
        quarantined.read_text(encoding="utf-8") == '[{"state":"taking a short break"}]'
    )
    assert quarantined.stat().st_mode & 0o777 == 0o600


def test_valid_public_history_is_not_quarantined(tmp_path) -> None:
    path = tmp_path / "message-history.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    save_history(
        path,
        [
            GenerationResult(
                "reviewing an integration",
                now,
                now + timedelta(minutes=10),
                "a" * 64,
            )
        ],
        8,
    )

    assert quarantine_invalid_history(path, 8, now) is None
    assert path.exists()


def test_candid_cache_persists_only_text_and_exact_turn_token(tmp_path) -> None:
    path = tmp_path / "candid-statuses.json"
    save_candid_status(
        path,
        "a" * 32,
        "b" * 32,
        "updating the adapter and checking the install path",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    loaded = load_candid_statuses(path)

    assert payload["schema_version"] == 3
    activity = payload["activities"][0]
    assert set(activity) == {
        "session_id",
        "turn_token",
        "text",
        "phase",
        "observed_at",
        "expires_at",
    }
    assert activity["session_id"] == "a" * 32
    assert activity["turn_token"] == "b" * 32
    assert loaded["a" * 32 + ":" + "b" * 32].text == activity["text"]


def test_incompatible_activity_cache_is_quarantined_for_continuous_recovery(
    tmp_path,
) -> None:
    path = tmp_path / "activity-window.json"
    path.write_text('{"schema_version":2,"activities":[]}', encoding="utf-8")
    quarantined = quarantine_invalid_activity(path, 8, datetime(2026, 1, 1, tzinfo=UTC))
    assert quarantined is not None and quarantined.exists()
    assert not path.exists()


def test_activity_loader_validates_each_record_and_save(tmp_path) -> None:
    path = tmp_path / "activity-window.json"
    valid_activity = {
        "session_id": "a" * 32,
        "turn_token": "b" * 32,
        "text": "updating the adapter",
        "phase": "settled",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-01T00:30:00+00:00",
    }
    payload = {
        "schema_version": 3,
        "activities": [
            {**valid_activity, "text": []},
            *[valid_activity for _ in range(31)],
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GenerationError):
        load_candid_statuses(path, limit=32)
    with pytest.raises(GenerationError):
        save_candid_status(path, "c" * 32, "d" * 32, "reviewing an integration")
    assert json.loads(path.read_text(encoding="utf-8")) == payload

    quarantined = quarantine_invalid_activity(
        path, 32, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert quarantined is not None and quarantined.exists()
    assert not path.exists()


def test_oversized_activity_window_cannot_resurface_provisional_status(
    tmp_path,
) -> None:
    path = tmp_path / "activity-window.json"
    settled = {
        "session_id": "a" * 32,
        "turn_token": "b" * 32,
        "text": "updating the adapter",
        "phase": "settled",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-01T00:30:00+00:00",
    }
    provisional = {
        **settled,
        "text": "reviewing an integration",
        "phase": "provisional",
    }
    payload = {
        "schema_version": 3,
        "activities": [
            settled,
            provisional,
            *[{**settled, "session_id": f"{index:032x}"} for index in range(31)],
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GenerationError):
        load_candid_statuses(path, limit=32)


@pytest.mark.parametrize("phase", [[], {}])
def test_candid_cache_rejects_non_string_phase(tmp_path, phase) -> None:
    path = tmp_path / "activity-window.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activities": [
                    {
                        "session_id": "a" * 32,
                        "turn_token": "b" * 32,
                        "text": "updating the adapter",
                        "phase": phase,
                        "observed_at": "2026-01-01T00:00:00+00:00",
                        "expires_at": "2026-01-01T00:30:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GenerationError):
        load_candid_statuses(path)


def test_candid_cache_rejects_unrepresentable_utc_timestamp(tmp_path) -> None:
    path = tmp_path / "activity-window.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activities": [
                    {
                        "session_id": "a" * 32,
                        "turn_token": "b" * 32,
                        "text": "updating the adapter",
                        "phase": "settled",
                        "observed_at": "0001-01-01T00:00:00+23:59",
                        "expires_at": "0001-01-01T00:01:00+23:59",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GenerationError):
        load_candid_statuses(path)


def test_candid_cache_rejects_invalid_retention_bound(tmp_path) -> None:
    path = tmp_path / "candid-statuses.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activities": [
                    {
                        "session_id": "a" * 32,
                        "turn_token": "b" * 32,
                        "text": "updating the adapter",
                        "phase": "settled",
                        "observed_at": "2026-01-01T00:00:00+00:00",
                        "expires_at": "2026-01-01T00:31:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GenerationError):
        load_candid_statuses(path)
