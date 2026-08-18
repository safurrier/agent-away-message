from __future__ import annotations

import json
import shutil
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_away_message.adapters import codex_record
from agent_away_message.cli import _drain_context_socket, _open_context_socket
from agent_away_message.config import Settings
from agent_away_message.context import (
    ContextJob,
    ContextQueue,
    deliver_context,
    process_one,
    receive_context,
)
from agent_away_message.identity import digest_session_id
from agent_away_message.models import ContextMode
from agent_away_message.privacy import (
    PrivacyError,
    validate_candid_status,
    validate_public_hint,
)


def test_queue_is_bounded_fifo_and_failed_summary_is_discarded() -> None:
    queue = ContextQueue(limit=1)
    assert queue.submit(ContextJob("a" * 32, "b" * 32, "first context"))
    assert not queue.submit(ContextJob("b" * 32, "c" * 32, "second context"))
    saved: list[tuple[str, str, str]] = []
    assert (
        process_one(
            queue,
            lambda _: "running tests",
            lambda identity, token, capsule, _phase: saved.append(
                (identity, token, capsule)
            ),
        )
        is True
    )
    assert saved == [("a" * 32, "b" * 32, "running tests")]
    assert not process_one(queue, lambda _: "running tests", lambda *_args: None)


def test_malformed_context_packets_are_discarded() -> None:
    assert receive_context(b'{"source":"private"}') is None
    assert receive_context(b'{"phase":[]}') is None
    queue = ContextQueue()
    assert queue.submit(ContextJob("a" * 32, "b" * 32, "private context"))

    with pytest.raises(OSError, match="disk unavailable"):
        process_one(
            queue,
            lambda _: "running tests",
            lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")),
        )


def test_daemon_drain_discards_malformed_phase_packet() -> None:
    state_dir = Path(tempfile.mkdtemp(dir="/tmp", prefix="aam-drain-"))
    settings = Settings(state_dir=state_dir, context_mode=ContextMode.CANDID)
    server = _open_context_socket(settings)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        client.sendto(
            json.dumps({"phase": []}).encode(), str(settings.context_socket_path)
        )
        queue = ContextQueue()
        _drain_context_socket(server, queue)
        assert len(queue) == 0
    finally:
        client.close()
        server.close()
        shutil.rmtree(state_dir)


def test_process_one_does_not_hide_programming_type_errors() -> None:
    queue = ContextQueue()
    assert queue.submit(ContextJob("a" * 32, "b" * 32, "private context"))

    with pytest.raises(TypeError, match="callback bug"):
        process_one(
            queue,
            lambda _: (_ for _ in ()).throw(TypeError("callback bug")),
            lambda *_: None,
        )


def test_settled_rejection_suppresses_late_provisional_in_memory() -> None:
    queue = ContextQueue(limit=2)
    session_id, token = "a" * 32, "b" * 32
    queue.suppress_provisional(session_id, token)
    assert not queue.submit(ContextJob(session_id, token, "late", "provisional"))
    assert queue.submit(ContextJob(session_id, "c" * 32, "other", "provisional"))


def test_settled_queue_work_wins_over_late_provisional_and_queue_pressure() -> None:
    queue = ContextQueue(limit=1)
    settled = ContextJob("a" * 32, "b" * 32, "settled", "settled")
    assert queue.submit(settled)
    assert not queue.submit(ContextJob("a" * 32, "b" * 32, "late", "provisional"))
    assert queue.take() == settled

    queue = ContextQueue(limit=1)
    assert queue.submit(ContextJob("a" * 32, "b" * 32, "provisional"))
    assert queue.submit(ContextJob("c" * 32, "d" * 32, "settled", "settled"))
    assert queue.take().phase == "settled"


def test_maximum_escaped_context_fits_the_local_socket_contract() -> None:
    directory = Path(tempfile.mkdtemp(dir="/tmp", prefix="aam-packet-"))
    socket_path = directory / "context.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(socket_path))
    try:
        source = "private-prefix" + '"' * 5_970 + "assistant-tail"
        assert deliver_context(socket_path, "a" * 32, "b" * 32, source)
        job = receive_context(server.recv(1_901))
    finally:
        server.close()
        shutil.rmtree(directory)

    assert job is not None
    assert 0 < len(job.source) < len(source)
    assert job.source.endswith("assistant-tail")


def test_semantic_identity_is_only_a_stable_opaque_digest(tmp_path) -> None:
    first = codex_record(
        '{"hook_event_name":"Stop","session_id":"billing-redesign"}',
        60,
        identity_key=tmp_path / "identity.key",
    )
    second = codex_record(
        '{"hook_event_name":"Stop","session_id":"billing-redesign"}',
        60,
        identity_key=tmp_path / "identity.key",
    )
    assert first.session_id == second.session_id
    assert "billing" not in first.session_id
    assert len(first.session_id) == 32


def test_concurrent_identity_bootstrap_is_stable(tmp_path) -> None:
    key_path = tmp_path / "state" / "identity.key"
    with ThreadPoolExecutor(max_workers=16) as executor:
        digests = list(
            executor.map(
                lambda _: digest_session_id("billing-redesign", key_path), range(64)
            )
        )

    assert len(set(digests)) == 1
    assert len(key_path.read_bytes()) == 32
    assert key_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "text",
    [
        "open /private/work",
        "visit https://example.test",
        "write alex@example.test",
        "run pytest now",
        "make deploy",
        "cargo test",
        "rm -rf temporary-files",
        "bash deploy.sh",
        "python -m pytest",
        "docker compose up",
        "aws deploy",
        "running go test",
        "running mise check",
        "using gh pr view",
        "running dotnet test",
        "running npx eslint",
        "running gradle build",
        "running mvn test",
        "running ruby checks",
        "reference deadbeefcafebabe",
        "mention private_source_identifier",
        "x" * 97,
    ],
)
def test_public_text_structurally_rejects_red_team_leaks(text: str) -> None:
    with pytest.raises(PrivacyError):
        validate_public_hint(text)


@pytest.mark.parametrize(
    "text",
    [
        "reviewing the api integration",
        "checking the branch logic",
        "reviewing the latest commit",
        "untangling a repository exception",
        "checking token refresh behavior",
        "ready to go",
        "writing ruby code",
        "using java patterns",
        "just checking the details",
    ],
)
def test_public_text_does_not_treat_ordinary_technical_words_as_commands(
    text: str,
) -> None:
    assert validate_public_hint(text) == text


def test_candid_status_allows_generic_work_without_requiring_friction() -> None:
    assert (
        validate_candid_status(
            "updating the adapter and checking the install path",
            "Update VenusBillingAdapter and check /private/install.py",
        )
        == "updating the adapter and checking the install path"
    )


def test_candid_status_rejects_distinctive_source_identifier() -> None:
    with pytest.raises(PrivacyError):
        validate_candid_status(
            "debugging VenusBilling integration failures",
            "Debug VenusBilling integration failures",
        )


@pytest.mark.parametrize(
    ("status", "source"),
    [
        ("working on somnambulist", "finish somnambulist presence integration"),
        (
            "updating acme payments integration",
            "Customer acme uses the private payments system",
        ),
    ],
)
def test_candid_status_does_not_infer_lowercase_source_identifiers(
    status: str, source: str
) -> None:
    assert validate_candid_status(status, source) == status


def test_candid_status_rejects_structurally_distinctive_acronym_identifier() -> None:
    with pytest.raises(PrivacyError):
        validate_candid_status("updating ACME configuration", "fix ACME configuration")


@pytest.mark.parametrize(
    "status",
    [
        "finished the migration successfully",
        "battling a production incident",
        "tracking down a production outage",
        "investigating why tests only fail in CI",
        "production system is down",
    ],
)
def test_candid_status_leaves_outcomes_complications_and_system_state_to_judge(
    status: str,
) -> None:
    assert validate_candid_status(status, "updating an adapter") == status
