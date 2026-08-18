"""Local-model generation, validation, deduplication, and bounded public history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from openai import OpenAI, OpenAIError, omit

from agent_away_message.config import ConfigError, validate_local_url
from agent_away_message.models import ActiveSession, ContextMode, GenerationResult
from agent_away_message.privacy import (
    PrivacyError,
    validate_candid_status,
    validate_message,
)

_TOKEN = re.compile(r"^[a-f0-9]{32}$")
_STAGE_TWO_CONTRACT = (
    'Return only a JSON object with a "state" string. Write one short, natural AIM-style '
    "activity status about the work itself, never about the user, agents, or harnesses. Keep "
    "it casual, dry, understated, and optionally a little odd or funny. Never imply the user "
    "is away or returning later. When approved candid activity is present, keep its broad "
    "task recognizable while expressing it as a status rather than copying it. Without "
    "approved candid activity, stay task-agnostic and describe only plurality or load; do "
    "not invent a task, project, problem, outcome, progress, or user action. Recent accepted "
    "statuses are negative style examples only: take a plainly different angle and do not "
    "reuse their central image, notable vocabulary, opening grammar, sentence skeleton, or "
    "cadence. Treat a topical word or phrase used in three or more recent statuses as "
    "saturated: avoid it when a truthful paraphrase or a different facet of the approved "
    "activity is available, but never sacrifice task grounding merely to vary wording. No "
    "files, commands, errors, names, URLs, exact counts, or copied recent wording. "
    "Use lowercase ASCII letters and spaces, with only comma, period, exclamation point, "
    "question mark, or apostrophe punctuation; keep the state under 96 characters."
)
_COMMAND_MAX_OUTPUT_BYTES = 65_536
_PRIVACY_JUDGE_REASONS = frozenset({"recognizable_or_too_specific"})


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """Validated public candid activity with opaque source provenance."""

    session_id: str
    turn_token: str
    text: str
    phase: str
    observed_at: datetime
    expires_at: datetime


_ACTIVITY_SCHEMA = 3
_ACTIVITY_TTL = timedelta(minutes=30)


class GenerationError(RuntimeError):
    """Raised when a local model cannot produce a publication-safe message."""


class CandidateRejectedError(GenerationError):
    """A safe validator rejection; rejected prose is never retained or displayed."""

    code = "malformed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class RecentDuplicateError(CandidateRejectedError):
    code = "recent_duplicate"


class PrivacyRejectedError(CandidateRejectedError):
    code = "privacy_rejected"


class MalformedCandidateError(CandidateRejectedError):
    code = "malformed"


class PrivacyJudgeRejectedError(CandidateRejectedError):
    code = "privacy_judge_rejected"


class PrivacyJudgeFailureError(CandidateRejectedError):
    code = "privacy_judge_failed"


class ModelRequestError(GenerationError):
    """Raised when the configured local model cannot return a completion."""


def safe_generation_error_code(error: Exception) -> str:
    """Return the public diagnostic code without exposing model or source prose."""
    if isinstance(error, CandidateRejectedError):
        return error.code
    return type(error).__name__


class CompletionClient(Protocol):
    """Minimal model seam used by deterministic tests and the daemon."""

    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class OpenAICompatibleClient:
    """OpenAI SDK adapter for a local OpenAI-compatible model server."""

    base_url: str
    model: str
    api_key: str = "local"
    timeout_seconds: float = 10.0
    seed: int | None = None
    chat_template_kwargs: dict[str, object] | None = None
    enable_thinking: bool | None = None

    def complete(self, prompt: str) -> str:
        try:
            validate_local_url(self.base_url)
        except ConfigError as error:
            raise ModelRequestError(
                "model URL must be an HTTP endpoint on this machine"
            ) from error
        try:
            extra_body: dict[str, object] = {}
            if self.chat_template_kwargs is not None:
                extra_body["chat_template_kwargs"] = self.chat_template_kwargs
            if self.enable_thinking is not None:
                extra_body["enable_thinking"] = self.enable_thinking
            response = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout_seconds,
            ).chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=60,
                seed=self.seed if self.seed is not None else omit,
                response_format={"type": "json_object"},
                extra_body=extra_body or None,
            )
            content = response.choices[0].message.content
        except (OpenAIError, IndexError, TypeError) as error:
            raise ModelRequestError("local model request failed") from error
        if not isinstance(content, str):
            raise ModelRequestError("local model response has no text completion")
        return content


@dataclass(frozen=True, slots=True)
class CommandClient:
    """Isolated generic JSON stdin/stdout command backend."""

    executable: str
    args: tuple[str, ...] = ()
    timeout_seconds: float = 10.0
    pass_environment: tuple[str, ...] = ()
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen

    def complete(self, prompt: str) -> str:
        request = json.dumps(
            {"schema_version": 1, "prompt": prompt}, separators=(",", ":")
        ).encode("utf-8")
        environment = {
            name: os.environ[name]
            for name in self.pass_environment
            if name in os.environ
        }
        process: subprocess.Popen[bytes] | None = None
        try:
            with tempfile.TemporaryDirectory(
                prefix="command-", dir="/tmp"
            ) as workspace:
                process = self.popen_factory(
                    [self.executable, *self.args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=False,
                    start_new_session=True,
                    cwd=workspace,
                    env=environment,
                )
                try:
                    output = self._send_and_read(process, request)
                except _CommandOutputTooLargeError as error:
                    raise ModelRequestError("command response invalid") from error
                except (subprocess.TimeoutExpired, OSError, ValueError) as error:
                    raise ModelRequestError("command request failed") from error
                finally:
                    # The leader may have exited while same-group descendants remain.
                    # Tear down that group before its isolated workspace disappears.
                    self._kill_and_reap(process)
        except (OSError, ValueError) as error:
            raise ModelRequestError("command request failed") from error
        if process is None or process.returncode != 0 or not isinstance(output, bytes):
            raise ModelRequestError("command request failed")
        try:
            payload = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelRequestError("command response invalid") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "completion"}
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("completion"), str)
        ):
            raise ModelRequestError("command response invalid")
        return payload["completion"]

    def _send_and_read(self, process: subprocess.Popen[bytes], request: bytes) -> bytes:
        """Exchange bounded protocol bytes without blocking outside the deadline."""
        # Lightweight injected process doubles retain the old protocol for unit tests;
        # real subprocesses always take the bounded streaming path below.
        if (
            getattr(process, "stdin", None) is None
            or getattr(process, "stdout", None) is None
        ):
            output, _ = process.communicate(request, timeout=self.timeout_seconds)
            if len(output) > _COMMAND_MAX_OUTPUT_BYTES:
                raise _CommandOutputTooLargeError()
            return output
        stdin = process.stdin
        stdout = process.stdout
        assert stdin is not None and stdout is not None
        stdin_fd = stdin.fileno()
        stdout_fd = stdout.fileno()
        os.set_blocking(stdin_fd, False)
        os.set_blocking(stdout_fd, False)
        output = bytearray()
        offset = 0
        stdout_open = True
        deadline = time.monotonic() + self.timeout_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(stdout, selectors.EVENT_READ)
            selector.register(stdin, selectors.EVENT_WRITE)
            while stdout_open or offset < len(request):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(
                        self.executable, self.timeout_seconds
                    )
                events = selector.select(remaining)
                if not events:
                    raise subprocess.TimeoutExpired(
                        self.executable, self.timeout_seconds
                    )
                for key, mask in events:
                    if key.fileobj is stdin and mask & selectors.EVENT_WRITE:
                        try:
                            offset += os.write(stdin_fd, request[offset:])
                        except BrokenPipeError:
                            # The command chose not to consume the request. Its output and
                            # exit status still decide whether that is a valid response.
                            offset = len(request)
                        if offset == len(request):
                            selector.unregister(stdin)
                            stdin.close()
                    if key.fileobj is stdout and mask & selectors.EVENT_READ:
                        chunk = os.read(stdout_fd, 8192)
                        if not chunk:
                            selector.unregister(stdout)
                            stdout_open = False
                        elif len(output) + len(chunk) > _COMMAND_MAX_OUTPUT_BYTES:
                            raise _CommandOutputTooLargeError()
                        else:
                            output.extend(chunk)
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return bytes(output)

    @staticmethod
    def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
        finally:
            # Do not call communicate here: it may buffer unread adversarial stdout.
            for stream in (
                getattr(process, "stdin", None),
                getattr(process, "stdout", None),
            ):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            try:
                process.wait()
            except (OSError, AttributeError):
                # Compatibility with minimal injected test doubles.
                pass


class _CommandOutputTooLargeError(Exception):
    """Internal sentinel: stdout crossed the fixed protocol boundary."""


def summarize_candid_context(client: CompletionClient, raw_context: str) -> str | None:
    """Reduce bounded source to one broad public activity or abstain."""
    if not raw_context or len(raw_context) > 6_000:
        raise GenerationError("candid context must be 1-6000 characters")
    raw_result = client.complete(
        'Return only a JSON object with a "text" string or null. Reduce the source to one '
        "short lowercase factual activity that a coding agent is doing. Keep one broad "
        "task category recognizable, but remove names, repositories, projects, products, "
        "files, branches, commands, exact errors, people, customers, incidents, and unusual "
        "identifiers. Return null when no useful public-safe activity remains. Do not invent "
        "a complication, outcome, progress claim, or motive. Example outputs are style-neutral "
        'and must not be copied: {"text":"checking model requirements"} or {"text":null}. '
        "Context follows:\n" + raw_context
    )
    try:
        payload = json.loads(raw_result)
        if payload is None:
            return None
        if isinstance(payload, dict):
            payload = payload.get("text")
        if payload is None:
            return None
        if not isinstance(payload, str):
            raise ValueError("schema")
        return validate_candid_status(payload, raw_context)
    except (json.JSONDecodeError, PrivacyError, ValueError) as error:
        raise PrivacyRejectedError() from error


def judge_public_activity(client: CompletionClient, activity: str) -> bool:
    """Light candidate-only recognizability check; source context never enters."""
    prompt = (
        'Return only JSON matching {"pass":boolean,"reason":'
        '"recognizable_or_too_specific"|null}. Judge only this proposed public coding '
        "activity. Reject it when it exposes or strongly suggests a recognizable person, "
        "project, product, repository, customer, file, branch, command, exact error, unusual "
        "identifier, or specific incident. Broad technical categories and ordinary tasks are "
        "allowed. Do not judge whether the activity is factually supported, successful, "
        "complete, natural, funny, or well written. Set reason to null on pass. Proposed "
        "public activity: " + json.dumps(activity)
    )
    try:
        payload = json.loads(client.complete(prompt))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"pass", "reason"}
            or type(payload.get("pass")) is not bool
        ):
            raise ValueError("schema")
        reason = payload.get("reason")
        if payload["pass"]:
            if reason is not None:
                raise ValueError("reason")
            return True
        if reason not in _PRIVACY_JUDGE_REASONS:
            raise ValueError("reason")
        return False
    except (json.JSONDecodeError, ValueError) as error:
        raise PrivacyJudgeFailureError() from error


def reduce_and_check_candid_activity(
    client: CompletionClient, raw_context: str
) -> str | None:
    """Use exactly two stage-one calls on an admitted candid path."""
    try:
        candidate = summarize_candid_context(client, raw_context)
    except CandidateRejectedError:
        return None
    if candidate is None:
        return None
    try:
        return candidate if judge_public_activity(client, candidate) else None
    except CandidateRejectedError:
        return None


def load_candid_statuses(path: Path, limit: int = 32) -> dict[str, ActivityRecord]:
    """Load the versioned rolling activity window (legacy caches fail closed)."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _ACTIVITY_SCHEMA
        ):
            raise ValueError("activity window schema is invalid")
        activities = payload.get("activities")
        if not isinstance(activities, list) or len(activities) > limit:
            raise ValueError("activities are invalid")
        validated: list[ActivityRecord] = []
        for item in activities:
            if not isinstance(item, dict):
                raise ValueError("activity is invalid")
            session_id, token, text = (
                item.get("session_id"),
                item.get("turn_token"),
                item.get("text"),
            )
            phase, observed, expiry = (
                item.get("phase"),
                item.get("observed_at"),
                item.get("expires_at"),
            )
            if (
                not isinstance(session_id, str)
                or not _TOKEN.fullmatch(session_id)
                or not isinstance(token, str)
                or not _TOKEN.fullmatch(token)
                or not isinstance(phase, str)
                or phase not in {"provisional", "settled"}
                or not isinstance(text, str)
                or not isinstance(observed, str)
                or not isinstance(expiry, str)
            ):
                raise ValueError("activity is invalid")
            observed_at, expires_at = (
                datetime.fromisoformat(observed),
                datetime.fromisoformat(expiry),
            )
            if observed_at.tzinfo is None or expires_at.tzinfo is None:
                raise ValueError("activity times need timezones")
            observed_utc, expiry_utc = (
                observed_at.astimezone(UTC),
                expires_at.astimezone(UTC),
            )
            if not observed_utc <= expiry_utc <= observed_utc + _ACTIVITY_TTL:
                raise ValueError("activity expiry is outside the retention bound")
            candidate = ActivityRecord(
                session_id,
                token,
                validate_message(text),
                phase,
                observed_utc,
                expiry_utc,
            )
            validated.append(candidate)
        result: dict[str, ActivityRecord] = {}
        for candidate in validated[-limit:]:
            # Durable state is monotonic: a late provisional packet cannot undo settled.
            key = f"{candidate.session_id}:{candidate.turn_token}"
            previous = result.get(key)
            if (
                previous is None
                or candidate.phase == "settled"
                or previous.phase != "settled"
            ):
                result[key] = candidate
        return result
    except (
        OSError,
        OverflowError,
        ValueError,
        json.JSONDecodeError,
        PrivacyError,
    ) as error:
        raise GenerationError("public activity window is invalid") from error


def remove_candid_status(
    path: Path, session_id: str, turn_token: str, limit: int = 32
) -> bool:
    """Remove one rejected turn's public activity without persisting a tombstone."""
    if not _TOKEN.fullmatch(session_id) or not _TOKEN.fullmatch(turn_token):
        raise GenerationError("activity identity is invalid")
    if not path.exists():
        return False
    items = load_candid_statuses(path, limit)
    removed = items.pop(f"{session_id}:{turn_token}", None) is not None
    if not removed:
        return False
    _write_candid_statuses(path, list(items.values()), limit)
    return True


def save_candid_status(
    path: Path,
    session_id: str,
    turn_token: str,
    status: str,
    limit: int = 32,
    *,
    phase: str = "settled",
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Persist only validated prose, opaque provenance, and bounded activity metadata."""
    if (
        not _TOKEN.fullmatch(session_id)
        or not _TOKEN.fullmatch(turn_token)
        or phase not in {"provisional", "settled"}
    ):
        raise GenerationError("activity identity is invalid")
    now = (observed_at or datetime.now(UTC)).astimezone(UTC)
    expiry = (expires_at or now + _ACTIVITY_TTL).astimezone(UTC)
    if not now <= expiry <= now + _ACTIVITY_TTL:
        raise GenerationError("activity expiry is outside the retention bound")
    items = load_candid_statuses(path, limit) if path.exists() else {}
    key = f"{session_id}:{turn_token}"
    previous = items.get(key)
    # Completion is monotonic even when a delayed provisional packet reaches disk later.
    if previous is not None and previous.phase == "settled" and phase == "provisional":
        return
    items[key] = ActivityRecord(
        session_id, turn_token, validate_message(status), phase, now, expiry
    )
    _write_candid_statuses(path, list(items.values()), limit)


def _write_candid_statuses(path: Path, items: list[ActivityRecord], limit: int) -> None:
    entries = [
        {
            "session_id": item.session_id,
            "turn_token": item.turn_token,
            "text": item.text,
            "phase": item.phase,
            "observed_at": item.observed_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
        }
        for item in items
    ]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"schema_version": _ACTIVITY_SCHEMA, "activities": entries[-limit:]}
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".activity-")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def aggregate_fingerprint(sessions: list[ActiveSession], mode: ContextMode) -> str:
    """Fingerprint only the public aggregate, never opaque session identities."""
    payload: dict[str, object] = {
        "count": len(sessions),
        "harnesses": sorted(session.harness.value for session in sessions),
        "mode": mode.value,
    }
    if mode is ContextMode.CANDID:
        payload["hints"] = _approved_context(sessions)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _approved_context(sessions: list[ActiveSession]) -> list[str]:
    """Return only separately validated public hints and candid activities."""
    return sorted(
        {
            phrase
            for session in sessions
            for phrase in (session.public_activity_hint, session.candid_status)
            if phrase
        }
    )


def build_prompt(
    sessions: list[ActiveSession],
    mode: ContextMode,
    recent_states: list[str] | None = None,
) -> str:
    """Build a narrow prompt containing only approved aggregate information."""
    harnesses = ", ".join(sorted({session.harness.value for session in sessions}))
    base = (
        _STAGE_TWO_CONTRACT
        + f" There are {len(sessions)} active agents from: {harnesses or 'none'}."
    )
    if recent_states:
        base += (
            " Recent accepted public statuses, supplied only as negative style examples: "
            + json.dumps(recent_states)
            + ". Make the new status plainly different in imagery, vocabulary, grammar, "
            "structure, and cadence. If a topical word or phrase appears in three or more "
            "of them, treat it as saturated and prefer a truthful paraphrase or another "
            "facet of the approved activity."
        )
    if mode is ContextMode.CANDID:
        hints = _approved_context(sessions)
        if hints:
            base += " Approved candid activity: " + json.dumps(hints)
    return base


def generate_state(
    client: CompletionClient,
    sessions: list[ActiveSession],
    mode: ContextMode,
    *,
    now: datetime,
    refresh_after: datetime,
    fingerprint: str,
    recent_states: list[str] | None = None,
) -> GenerationResult:
    """Generate one state with one stage-two call and deterministic validation."""
    prompt = build_prompt(sessions, mode, recent_states)
    candid_evidence = _approved_context(sessions) if mode is ContextMode.CANDID else []
    state = _parse_state(
        client.complete(prompt),
        recent_states or [],
        candid_evidence,
        mode is ContextMode.CANDID,
    )
    return GenerationResult(
        state=state,
        generated_at=now,
        refresh_after=refresh_after,
        aggregate_fingerprint=fingerprint,
    )


def _parse_state(
    raw: str,
    recent_states: list[str],
    candid_evidence: list[str],
    candid_mode: bool,
) -> str:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("state"), str):
            raise ValueError("schema")
        state = validate_message(payload["state"])
        if candid_mode:
            state = validate_candid_status(state, " ".join(candid_evidence))
        recent = {item.casefold() for item in recent_states}
        if state.casefold() in recent:
            raise RecentDuplicateError()
        return state
    except CandidateRejectedError:
        raise
    except PrivacyError as error:
        raise PrivacyRejectedError() from error
    except (json.JSONDecodeError, ValueError) as error:
        raise MalformedCandidateError() from error


def quarantine_invalid_activity(
    path: Path, limit: int, now: datetime | None = None
) -> Path | None:
    """Quarantine incompatible public activity caches for continuous recovery."""
    try:
        load_candid_statuses(path, limit)
    except GenerationError:
        if not path.exists():
            return None
        stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = path.with_name(f"{path.stem}.invalid-{stamp}{path.suffix}")
        try:
            path.replace(target)
            target.chmod(0o600)
        except FileNotFoundError:
            return None
        except OSError as move_error:
            raise GenerationError(
                "invalid public activity could not be quarantined"
            ) from move_error
        return target
    return None


def quarantine_invalid_history(
    path: Path, limit: int, now: datetime | None = None
) -> Path | None:
    """Move an invalid public-only history cache aside so a live daemon can recover."""
    try:
        load_history(path, limit)
    except GenerationError:
        if not path.exists():
            return None
        timestamp = (
            (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        )
        target = path.with_name(f"{path.stem}.invalid-{timestamp}{path.suffix}")
        try:
            path.replace(target)
            target.chmod(0o600)
        except FileNotFoundError:
            return None
        except OSError as move_error:
            raise GenerationError(
                "invalid public history could not be quarantined"
            ) from move_error
        return target
    return None


def load_history(path: Path, limit: int) -> list[GenerationResult]:
    """Load only validated public outputs from the bounded cache."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("history must be a list")
        results: list[GenerationResult] = []
        for item in payload[-limit:]:
            if not isinstance(item, dict):
                raise ValueError("history item must be an object")
            generated_at = datetime.fromisoformat(str(item["generated_at"]))
            refresh_after = datetime.fromisoformat(str(item["refresh_after"]))
            fingerprint = str(item["aggregate_fingerprint"])
            if generated_at.tzinfo is None or refresh_after.tzinfo is None:
                raise ValueError("history times must include timezones")
            if len(fingerprint) != 64:
                raise ValueError("history fingerprint is invalid")
            results.append(
                GenerationResult(
                    state=validate_message(str(item["state"])),
                    generated_at=generated_at.astimezone(UTC),
                    refresh_after=refresh_after.astimezone(UTC),
                    aggregate_fingerprint=fingerprint,
                )
            )
        return results
    except (KeyError, OSError, ValueError, json.JSONDecodeError, PrivacyError) as error:
        raise GenerationError("public message history is invalid") from error


def save_history(path: Path, history: list[GenerationResult], limit: int) -> None:
    """Atomically persist a bounded list containing only validated public output."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    payload = [
        {
            "state": item.state,
            "generated_at": item.generated_at.isoformat(),
            "refresh_after": item.refresh_after.isoformat(),
            "aggregate_fingerprint": item.aggregate_fingerprint,
        }
        for item in history[-limit:]
    ]
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".history-")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def valid_fallback(
    history: list[GenerationResult], now: datetime, max_age_seconds: int
) -> GenerationResult | None:
    """Return the latest output only while its explicit fallback bound holds."""
    if not history:
        return None
    latest = history[-1]
    if latest.generated_at + timedelta(seconds=max_age_seconds) < now:
        return None
    return latest
