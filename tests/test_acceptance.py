from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_away_message.adapters import codex_record, pi_record
from agent_away_message.cli import (
    AppContext,
    _DaemonRuntime,
    _drain_context_socket,
    _open_context_socket,
    cli,
)
from agent_away_message.config import Settings
from agent_away_message.context import ContextJob, ContextQueue, process_one
from agent_away_message.daemon import (
    PreviewGenerationError,
    load_active_sessions,
    refresh,
)
from agent_away_message.generation import (
    CompletionClient,
    ModelRequestError,
    aggregate_fingerprint,
    load_candid_statuses,
    load_history,
    reduce_and_check_candid_activity,
    save_candid_status,
    save_history,
)
from agent_away_message.models import (
    ActiveSession,
    ContextMode,
    GenerationResult,
    Harness,
    LifecycleEvent,
    LifecycleRecord,
    PublicationMode,
    turn_token_for,
)
from agent_away_message.publisher import MemoryPublisher
from agent_away_message.store import EventStore


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Proposed public activity:" in prompt:
            return '{"pass":true,"reason":null}'
        return self.responses.pop(0)


class RequestFailingClient:
    def complete(self, _prompt: str) -> str:
        raise ModelRequestError("local model request failed")


class EmptySocket:
    def recv(self, _size: int) -> bytes:
        raise BlockingIOError

    def close(self) -> None:
        pass


def test_daemon_stage_one_transport_failure_makes_zero_stage_two_calls(
    tmp_path,
) -> None:
    now = datetime.now(UTC)
    settings = Settings(
        state_dir=tmp_path,
        context_mode=ContextMode.CANDID,
        publication_mode=PublicationMode.DISCORD,
    )
    EventStore(settings.events_path).append(
        LifecycleRecord(
            1,
            Harness.PI,
            "a" * 32,
            LifecycleEvent.STARTED,
            now,
            now + timedelta(hours=1),
            "test",
        )
    )
    save_history(
        settings.history_path,
        [
            GenerationResult(
                "the current task has a quiet second opinion",
                now,
                now + timedelta(minutes=10),
                "f" * 64,
            )
        ],
        settings.history_limit,
    )
    queue = ContextQueue()
    assert queue.submit(ContextJob("a" * 32, "b" * 32, "private source", "settled"))
    stage_two = FakeClient(['{"state":"should not run"}'])
    publisher = MemoryPublisher()
    runtime = _DaemonRuntime(
        AppContext(settings, True, None),
        RequestFailingClient(),
        stage_two,
        publisher,
        EmptySocket(),
        None,
        queue,
    )
    admitted, presence = runtime.step()
    assert admitted
    assert presence is not None
    assert presence.state == "the current task has a quiet second opinion"
    assert publisher.last == presence
    assert runtime.generation_error == "ModelRequestError"
    assert stage_two.prompts == []


def test_preview_stage_one_transport_failure_remains_visible(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path, context_mode=ContextMode.CANDID)
    queue = ContextQueue()
    assert queue.submit(ContextJob("a" * 32, "b" * 32, "private source", "settled"))
    runtime = _DaemonRuntime(
        AppContext(settings, True, None),
        RequestFailingClient(),
        FakeClient(['{"state":"should not run"}']),
        MemoryPublisher(),
        EmptySocket(),
        None,
        queue,
    )
    with pytest.raises(ModelRequestError):
        runtime.step()


def test_settled_rejection_removes_provisional_and_forces_generic(tmp_path) -> None:
    now = datetime.now(UTC)
    settings = Settings(state_dir=tmp_path, context_mode=ContextMode.CANDID)
    session_id, token = "a" * 32, "b" * 32
    EventStore(settings.events_path).append(
        LifecycleRecord(
            1,
            Harness.PI,
            session_id,
            LifecycleEvent.STARTED,
            now,
            now + timedelta(hours=1),
            "test",
        )
    )
    save_candid_status(
        settings.candid_path,
        session_id,
        token,
        "reviewing an integration",
        phase="provisional",
    )
    candid_session = ActiveSession(
        Harness.PI,
        session_id,
        now + timedelta(hours=1),
        None,
        token,
        "reviewing an integration",
    )
    save_history(
        settings.history_path,
        [
            GenerationResult(
                "integration paperwork remains undefeated",
                now,
                now + timedelta(minutes=10),
                aggregate_fingerprint([candid_session], ContextMode.CANDID),
            )
        ],
        settings.history_limit,
    )
    queue = ContextQueue()
    assert queue.submit(
        ContextJob(session_id, token, "settled private source", "settled")
    )
    stage_one = FakeClient(['{"text":null}'])
    stage_two = FakeClient(
        ['{"state":"several threads, none volunteering for minutes"}']
    )
    runtime = _DaemonRuntime(
        AppContext(settings, True, None),
        stage_one,
        stage_two,
        MemoryPublisher(),
        EmptySocket(),
        None,
        queue,
    )
    admitted, presence = runtime.step()
    assert admitted and presence is not None
    assert len(stage_two.prompts) == 1
    assert "Approved candid activity" not in stage_two.prompts[0]
    assert "reviewing an integration" not in stage_two.prompts[0]
    assert load_candid_statuses(settings.candid_path) == {}
    assert not queue.submit(
        ContextJob(session_id, token, "late provisional source", "provisional")
    )
    assert load_candid_statuses(settings.candid_path) == {}
    assert (
        load_history(settings.history_path, settings.history_limit)[-1].state
        == presence.state
    )


def test_codex_native_events_use_fallback_and_never_store_payload() -> None:
    record = codex_record(
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "private tool",
                "cwd": "/private/project",
            }
        ),
        60,
        fallback_session_id="%12",
    )

    assert record.event.value == "codex.pre_tool_use"
    assert len(record.session_id) == 32
    assert "%12" not in record.session_id
    assert set(record.to_json()) == {
        "schema_version",
        "harness",
        "session_id",
        "event",
        "occurred_at",
        "expires_at",
        "provenance",
        "public_activity_hint",
    }


def test_codex_context_never_enters_the_lifecycle_record() -> None:
    record = codex_record(
        '{"hook_event_name":"UserPromptSubmit","session_id":"s1","prompt":"private source"}',
        60,
    )

    assert record.public_activity_hint is None
    assert "private source" not in json.dumps(record.to_json())


def test_pi_native_events_preserve_exact_source_observations() -> None:
    assert (
        pi_record('{"event":"agent_start","session_id":"7"}', 60).event.value
        == "pi.agent_start"
    )
    assert (
        pi_record('{"event":"agent_end","session_id":"7"}', 60).event.value
        == "pi.agent_end"
    )
    assert (
        pi_record('{"event":"session_shutdown","session_id":"7"}', 60).event.value
        == "pi.session_shutdown"
    )


def test_setup_merges_all_native_codex_event_groups_idempotently(
    tmp_path: Path,
) -> None:
    config = tmp_path / "hooks.json"
    config.write_text(
        '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"existing-codex-hook"}]}]}}',
        encoding="utf-8",
    )
    runner = CliRunner()
    args = ["--json", "setup", "--codex-config", str(config)]

    first = runner.invoke(cli, args)
    second = runner.invoke(cli, args)
    hooks = json.loads(config.read_text(encoding="utf-8"))["hooks"]

    assert first.exit_code == second.exit_code == 0
    assert json.loads(first.output)["changed"] is True
    assert json.loads(second.output)["changed"] is False
    assert hooks["Stop"][0]["hooks"][0]["command"] == "existing-codex-hook"
    for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        assert (
            sum(
                command["command"].endswith(" codex-hook")
                for group in hooks[event]
                for command in group["hooks"]
            )
            == 1
        )


def test_refresh_regenerates_when_aggregate_state_changes(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path,
        generation_cadence_min_seconds=480,
        generation_cadence_max_seconds=1200,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = EventStore(settings.events_path)
    store.append(
        LifecycleRecord(
            1,
            Harness.PI,
            "11111111111111111111111111111111",
            LifecycleEvent.STARTED,
            now,
            now + timedelta(hours=1),
            "test",
        )
    )
    client = FakeClient(
        ['{"state":"chasing a flaky test"}', '{"state":"herding agents steadily"}']
    )
    publisher = MemoryPublisher()
    refresh(settings, client, publisher, now)
    store.append(
        LifecycleRecord(
            1,
            Harness.CODEX,
            "22222222222222222222222222222222",
            LifecycleEvent.CODEX_PRE_TOOL_USE,
            now,
            now + timedelta(hours=1),
            "test",
        )
    )
    refresh(settings, client, publisher, now + timedelta(seconds=1))

    assert len(client.prompts) == 2
    assert "Recent accepted public statuses" in client.prompts[1]
    assert "negative style examples" in client.prompts[1]


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (FakeClient(["bad", "bad"]), "malformed"),
        (RequestFailingClient(), "ModelRequestError"),
    ],
)
def test_preview_reports_safe_generation_failure_class(
    tmp_path: Path, client: CompletionClient, reason: str
) -> None:
    settings = Settings(state_dir=tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    sessions = [ActiveSession(Harness.PI, "1" * 32, now + timedelta(hours=1), None)]

    with pytest.raises(PreviewGenerationError) as captured:
        refresh(
            settings,
            client,
            MemoryPublisher(),
            now,
            sessions=sessions,
        )

    assert captured.value.reason == reason
    assert reason in str(captured.value)


def test_discord_expired_fallback_clears_presence(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path,
        publication_mode=PublicationMode.DISCORD,
        retain_last_seconds=1,
        generation_cadence_min_seconds=1,
        generation_cadence_max_seconds=1,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    EventStore(settings.events_path).append(
        LifecycleRecord(
            1,
            Harness.PI,
            "11111111111111111111111111111111",
            LifecycleEvent.STARTED,
            now,
            now + timedelta(hours=1),
            "test",
        )
    )
    publisher = MemoryPublisher()
    refresh(settings, FakeClient(['{"state":"chasing a flaky test"}']), publisher, now)
    errors = []
    result = refresh(
        settings,
        RequestFailingClient(),
        publisher,
        now + timedelta(seconds=2),
        on_generation_error=errors.append,
    )

    assert result is None
    assert publisher.last is None
    assert [type(error).__name__ for error in errors] == ["ModelRequestError"]


def test_discord_candidate_rejection_reuses_recent_valid_presence(
    tmp_path: Path,
) -> None:
    settings = Settings(
        state_dir=tmp_path,
        publication_mode=PublicationMode.DISCORD,
        generation_cadence_min_seconds=1,
        generation_cadence_max_seconds=1,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    sessions = [
        ActiveSession(
            Harness.PI,
            "1" * 32,
            now + timedelta(hours=1),
            None,
        )
    ]
    publisher = MemoryPublisher()
    first = refresh(
        settings,
        FakeClient(['{"state":"reviewing an integration"}']),
        publisher,
        now,
        sessions=sessions,
    )
    errors = []

    second = refresh(
        settings,
        FakeClient(["bad", "bad"]),
        publisher,
        now + timedelta(seconds=2),
        sessions=sessions,
        on_generation_error=errors.append,
    )

    assert first is not None
    assert second == first
    assert [getattr(error, "code", type(error).__name__) for error in errors] == [
        "malformed"
    ]


def test_latest_recent_candid_activity_survives_a_new_turn(tmp_path: Path) -> None:
    settings = Settings(state_dir=tmp_path, context_mode=ContextMode.CANDID)
    first = pi_record(
        '{"event":"input","session_id":"pi-1","text":"first"}',
        60,
        identity_key=settings.identity_key_path,
    )
    EventStore(settings.events_path).append(first)
    save_candid_status(
        settings.candid_path,
        first.session_id,
        turn_token_for(
            first.harness,
            first.session_id,
            first.event,
            first.occurred_at,
        ),
        "updating the adapter",
    )
    CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--context-mode",
            "candid",
            "pi-hook",
        ],
        input='{"event":"input","session_id":"pi-1","text":"second private turn"}',
    )
    client = FakeClient(['{"state":"herding agents steadily"}'])

    refresh(settings, client, MemoryPublisher())

    assert "updating the adapter" in client.prompts[0]
    assert "second private turn" not in settings.events_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("hook", "input_payload", "completion_payload"),
    [
        (
            "pi-hook",
            {"event": "input", "session_id": "paired-pi", "text": "reviewing tests"},
            {
                "event": "agent_end",
                "session_id": "paired-pi",
                "candid_context": "updating tests",
            },
        ),
        (
            "codex-hook",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "paired-codex",
                "prompt": "reviewing tests",
            },
            {
                "hook_event_name": "Stop",
                "session_id": "paired-codex",
                "last_assistant_message": "updating tests",
            },
        ),
    ],
)
def test_candid_completion_pairs_with_older_input_without_overwriting_newer_turn(
    hook: str,
    input_payload: dict[str, str],
    completion_payload: dict[str, str],
) -> None:
    state_dir = Path(tempfile.mkdtemp(dir="/tmp", prefix="aam-pairing-"))
    settings = Settings(state_dir=state_dir, context_mode=ContextMode.CANDID)
    runner, socket = CliRunner(), _open_context_socket(settings)
    try:
        common = ["--state-dir", str(state_dir), "--context-mode", "candid", hook]

        def invoke(payload: dict[str, str]) -> None:
            result = runner.invoke(cli, common, input=json.dumps(payload))
            assert result.exit_code == 0

        invoke(input_payload)
        invoke(
            {
                **input_payload,
                **(
                    {"text": "checking tests"}
                    if hook == "pi-hook"
                    else {"prompt": "checking tests"}
                ),
            }
        )
        invoke(completion_payload)
        records = [
            LifecycleRecord.from_json(json.loads(line))
            for line in settings.events_path.read_text(encoding="utf-8").splitlines()
        ]
        first_token = turn_token_for(
            records[0].harness,
            records[0].session_id,
            records[0].event,
            records[0].occurred_at,
        )
        second_token = turn_token_for(
            records[1].harness,
            records[1].session_id,
            records[1].event,
            records[1].occurred_at,
        )
        queue = ContextQueue()
        _drain_context_socket(socket, queue)
        while process_one(
            queue,
            lambda source: source,
            lambda session_id, token, status, phase: save_candid_status(
                settings.candid_path, session_id, token, status, phase=phase
            ),
        ):
            pass
        activities = load_candid_statuses(settings.candid_path)
        session_id = records[0].session_id
        assert activities[f"{session_id}:{first_token}"].phase == "settled"
        assert activities[f"{session_id}:{second_token}"].phase == "provisional"

        invoke(completion_payload)
        _drain_context_socket(socket, queue)
        assert process_one(
            queue,
            lambda source: source,
            lambda session_id, token, status, phase: save_candid_status(
                settings.candid_path, session_id, token, status, phase=phase
            ),
        )
        assert (
            load_candid_statuses(settings.candid_path)[
                f"{session_id}:{second_token}"
            ].phase
            == "settled"
        )
    finally:
        socket.close()
        shutil.rmtree(state_dir)


def test_interleaved_candid_hooks_consolidate_rolling_activity_without_raw_leaks() -> (
    None
):
    """Exercise native adapters, CLI ingress, socket queue, activity store, and stage two."""
    state_dir = Path(tempfile.mkdtemp(dir="/tmp", prefix="aam-interleaved-"))
    settings = Settings(state_dir=state_dir, context_mode=ContextMode.CANDID)
    runner, socket = CliRunner(), _open_context_socket(settings)
    pi_raw = "PI_CANARY_OrchidLedger_881"
    codex_raw = "CODEX_CANARY_NebulaQueue_992"
    completion_raw = "COMPLETION_CANARY_SecretFix_771"
    commands: list[str] = []
    try:

        def hook(command: str, payload: dict[str, str]) -> None:
            result = runner.invoke(
                cli,
                ["--state-dir", str(state_dir), "--context-mode", "candid", command],
                input=json.dumps(payload),
            )
            commands.append(result.output + str(result.exception))
            assert result.exit_code == 0

        # Pi completion A arrives after Pi input B, so it must settle A without
        # replacing B's provisional activity. Codex exercises the same queue.
        hook("pi-hook", {"event": "input", "session_id": "pi", "text": pi_raw})
        hook(
            "codex-hook",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "codex",
                "prompt": codex_raw,
            },
        )
        hook("pi-hook", {"event": "input", "session_id": "pi", "text": "later turn"})
        hook(
            "pi-hook",
            {
                "event": "agent_end",
                "session_id": "pi",
                "candid_context": completion_raw,
            },
        )
        hook(
            "codex-hook",
            {
                "hook_event_name": "Stop",
                "session_id": "codex",
                "last_assistant_message": completion_raw,
            },
        )

        queue = ContextQueue()
        _drain_context_socket(socket, queue)
        model = FakeClient(
            [
                '{"text":"reviewing an integration"}',
                '{"text":"updating the adapter"}',
                '{"text":"checking the install path"}',
                '{"state":"reviewing the integration and updating the adapter"}',
            ]
        )
        while process_one(
            queue,
            lambda source: reduce_and_check_candid_activity(model, source),
            lambda session_id, token, status, phase: save_candid_status(
                settings.candid_path, session_id, token, status, phase=phase
            ),
        ):
            pass
        activities = load_candid_statuses(settings.candid_path)
        assert {item.phase for item in activities.values()} >= {
            "settled",
            "provisional",
        }
        sessions = load_active_sessions(settings, datetime.now(UTC))
        assert len(sessions) == 2  # Exact count remains lifecycle-derived.
        presence = refresh(settings, model, MemoryPublisher())
        assert presence is not None and presence.details == "2 coding agents active"
        stage_two = model.prompts[-1]
        assert "checking the install path" in stage_two
        assert "updating the adapter" in stage_two

        # Source shutdown removes its activity; expiry removes the other without
        # allowing advisory data to change the exact lifecycle count.
        hook("pi-hook", {"event": "session_shutdown", "session_id": "pi"})
        assert len(load_active_sessions(settings, datetime.now(UTC))) == 1
        expired_observed = datetime.now(UTC) - timedelta(minutes=31)
        for activity in load_candid_statuses(settings.candid_path).values():
            save_candid_status(
                settings.candid_path,
                activity.session_id,
                activity.turn_token,
                activity.text,
                phase=activity.phase,
                observed_at=expired_observed,
                expires_at=expired_observed + timedelta(minutes=30),
            )
        assert (
            load_active_sessions(settings, datetime.now(UTC))[0].candid_status is None
        )

        public = "".join(
            path.read_text(encoding="utf-8")
            for path in (
                settings.events_path,
                settings.candid_path,
                settings.history_path,
            )
            if path.exists()
        ) + "".join(commands)
        for canary in (pi_raw, codex_raw, completion_raw):
            assert canary not in public
    finally:
        socket.close()
        shutil.rmtree(state_dir)


def test_locked_mode_never_sends_prompt_text_to_model(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "--context-mode",
            ContextMode.GENERIC.value,
            "codex-hook",
        ],
        input='{"hook_event_name":"UserPromptSubmit","session_id":"s1","prompt":"private source"}',
    )

    assert result.exit_code == 0
    assert result.output == ""


@pytest.mark.parametrize(
    ("hook", "start_payload", "end_payload"),
    [
        (
            "pi-hook",
            {"event": "input", "session_id": "pi-1", "text": "private start"},
            {
                "event": "agent_end",
                "session_id": "pi-1",
                "candid_context": "User: Fix VenusBilling adapter\nAssistant: Updated it and checked installation.",
            },
        ),
        (
            "codex-hook",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "codex-1",
                "prompt": "Fix VenusBilling adapter",
            },
            {
                "hook_event_name": "Stop",
                "session_id": "codex-1",
                "last_assistant_message": "Updated the private adapter and checked installation.",
            },
        ),
    ],
)
def test_candid_native_completion_reaches_presence_without_source_leak(
    hook: str,
    start_payload: dict[str, str],
    end_payload: dict[str, str],
) -> None:
    state_dir = Path(tempfile.mkdtemp(dir="/tmp", prefix="aam-candid-"))
    settings = Settings(state_dir=state_dir, context_mode=ContextMode.CANDID)
    runner = CliRunner()
    socket = _open_context_socket(settings)
    try:
        common = [
            "--state-dir",
            str(state_dir),
            "--context-mode",
            "candid",
            hook,
        ]
        assert (
            runner.invoke(cli, common, input=json.dumps(start_payload)).exit_code == 0
        )
        assert runner.invoke(cli, common, input=json.dumps(end_payload)).exit_code == 0
        queue = ContextQueue()
        _drain_context_socket(socket, queue)
        model = FakeClient(
            [
                '{"text":"updating the adapter and checking the install path"}',
                '{"state":"checking the latest changes"}',
            ]
        )
        assert process_one(
            queue,
            lambda source: reduce_and_check_candid_activity(model, source),
            lambda session_id, token, status, phase: save_candid_status(
                settings.candid_path, session_id, token, status, phase=phase
            ),
        )
        publisher = MemoryPublisher()
        presence = refresh(settings, model, publisher)
        persisted = "".join(
            path.read_text(encoding="utf-8")
            for path in (
                settings.events_path,
                settings.candid_path,
                settings.history_path,
            )
            if path.exists()
        )
    finally:
        socket.close()
        settings.context_socket_path.unlink(missing_ok=True)
        shutil.rmtree(state_dir)

    assert presence is not None
    assert presence.state == "checking the latest changes"
    assert "updating the adapter and checking the install path" in model.prompts[-2]
    assert "VenusBilling" not in persisted
    assert "VenusBilling" not in model.prompts[-1]
