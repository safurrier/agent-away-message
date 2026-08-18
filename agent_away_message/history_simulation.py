"""Read-only end-to-end replay of historic presence through the public pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_away_message.generation import (
    CompletionClient,
    GenerationError,
    ModelRequestError,
    aggregate_fingerprint,
    generate_state,
    reduce_and_check_candid_activity,
    safe_generation_error_code,
)
from agent_away_message.models import ActiveSession, ContextMode, Harness

MAX_SOURCE_CHARS = 6_000


@dataclass(frozen=True, slots=True)
class _Interval:
    harness: Harness
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class _Sample:
    harness: Harness
    at: datetime
    source: str


def simulate_history(
    stage_one: CompletionClient,
    stage_two: CompletionClient,
    *,
    mode: ContextMode,
    codex_root: Path,
    pi_root: Path,
    since: datetime,
    now: datetime,
    ttl_seconds: int,
    max_examples: int,
) -> dict[str, object]:
    """Return raw-free aggregate history plus end-to-end public replay examples."""
    intervals: list[_Interval] = []
    samples: list[_Sample] = []
    accepted = {Harness.CODEX: 0, Harness.PI: 0}
    skipped = 0
    older_files_skipped = 0
    for harness, root, parser in (
        (Harness.CODEX, codex_root, _parse_codex),
        (Harness.PI, pi_root, _parse_pi),
    ):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            if not _file_could_overlap(path, since, ttl_seconds):
                older_files_skipped += 1
                continue
            parsed = parser(
                path,
                ttl_seconds,
                collect_samples=mode is ContextMode.CANDID,
            )
            if parsed is None:
                skipped += 1
                continue
            file_intervals, file_samples = parsed
            overlapping = [
                interval
                for interval in file_intervals
                if interval.ended_at > since and interval.started_at <= now
            ]
            if not overlapping:
                continue
            intervals.extend(
                _Interval(harness, max(interval.started_at, since), interval.ended_at)
                for interval in overlapping
            )
            samples.extend(item for item in file_samples if since <= item.at <= now)
            accepted[harness] += 1

    timeline = _timeline(intervals, now)
    sampled = (
        sorted(samples, key=lambda item: item.at)[-max_examples:]
        if max_examples
        else []
    )
    if mode is ContextMode.CANDID:
        examples, counters = _replay_candid_samples(
            stage_one, stage_two, sampled, timeline
        )
    else:
        examples, counters = _replay_generic_timeline(
            stage_two, sampled, timeline, max_examples
        )
    peak_agents = max(
        (
            item["active_agents"]
            for item in timeline
            if isinstance(item["active_agents"], int)
        ),
        default=0,
    )
    return {
        "approximate": True,
        "since": since.astimezone(UTC).isoformat(),
        "until": now.astimezone(UTC).isoformat(),
        "sources": {
            "codex_sessions": accepted[Harness.CODEX],
            "pi_sessions": accepted[Harness.PI],
            "malformed_or_unrecognized_skipped": skipped,
            "older_files_skipped": older_files_skipped,
        },
        "peak_agents": peak_agents,
        "timeline": timeline,
        "replay_examples": examples,
        "candidate_sampling": {"attempted": len(sampled), **counters},
        "stable_refresh_range_minutes": [8, 20],
        "notes": [
            "Counts approximate sessions from recognized history timestamps and the configured heartbeat TTL.",
            (
                "Generic replay collected aggregate timing only; source message fields were not extracted and stage one was not called."
                if mode is ContextMode.GENERIC
                else "Candid replay bounded source for local stage one; output contains only validated public activity, final status, fallback markers, and safe errors."
            ),
        ],
    }


def _replay_candid_samples(
    stage_one: CompletionClient,
    stage_two: CompletionClient,
    samples: list[_Sample],
    timeline: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    examples: list[dict[str, object]] = []
    recent_states: list[str] = []
    admitted = generic_fallbacks = errors = 0
    for index, sample in enumerate(samples):
        count, harnesses = _aggregate_at(timeline, sample.at, sample.harness)
        activity: str | None = None
        fallback = False
        safe_error: str | None = None
        try:
            activity = reduce_and_check_candid_activity(stage_one, sample.source)
        except ModelRequestError as error:
            examples.append(
                {
                    "at": sample.at.astimezone(UTC).isoformat(),
                    "harness": sample.harness.value,
                    "active_agents": count,
                    "public_activity": None,
                    "status": None,
                    "generic_fallback": False,
                    "error": safe_generation_error_code(error),
                }
            )
            errors += 1
            continue
        if activity is None:
            fallback = True
        if fallback:
            generic_fallbacks += 1
        else:
            admitted += 1
        sessions = [
            ActiveSession(
                harnesses[position % len(harnesses)],
                f"replay-{index}-{position}",
                sample.at + timedelta(seconds=ttl_seconds_for_replay()),
                None,
                candid_status=activity if position == 0 else None,
            )
            for position in range(count)
        ]
        mode = ContextMode.CANDID if activity is not None else ContextMode.GENERIC
        try:
            result = generate_state(
                stage_two,
                sessions,
                mode,
                now=sample.at,
                refresh_after=sample.at + timedelta(minutes=12),
                fingerprint=aggregate_fingerprint(sessions, mode),
                recent_states=recent_states[-12:],
            )
            status = result.state
            recent_states.append(status)
        except GenerationError as error:
            status = None
            safe_error = safe_generation_error_code(error)
            errors += 1
        examples.append(
            {
                "at": sample.at.astimezone(UTC).isoformat(),
                "harness": sample.harness.value,
                "active_agents": count,
                "public_activity": activity,
                "status": status,
                "generic_fallback": fallback,
                "error": safe_error,
            }
        )
    return examples, {
        "admitted": admitted,
        "generic_fallbacks": generic_fallbacks,
        "generation_errors": errors,
    }


def _replay_generic_timeline(
    stage_two: CompletionClient,
    samples: list[_Sample],
    timeline: list[dict[str, object]],
    max_examples: int,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    points = [item for item in timeline if item.get("active_agents")]
    selected = points[-max_examples:] if max_examples else []
    examples: list[dict[str, object]] = []
    recent_states: list[str] = []
    errors = 0
    for index, item in enumerate(selected):
        at = datetime.fromisoformat(str(item["at"]))
        active_agents = item.get("active_agents")
        harness_values = item.get("harnesses")
        if not isinstance(active_agents, int) or not isinstance(harness_values, list):
            continue
        count = active_agents
        harnesses = [
            Harness(value) for value in harness_values if isinstance(value, str)
        ]
        if not harnesses:
            continue
        sessions = [
            ActiveSession(
                harnesses[position % len(harnesses)],
                f"generic-replay-{index}-{position}",
                at + timedelta(seconds=ttl_seconds_for_replay()),
                None,
            )
            for position in range(count)
        ]
        try:
            result = generate_state(
                stage_two,
                sessions,
                ContextMode.GENERIC,
                now=at,
                refresh_after=at + timedelta(minutes=12),
                fingerprint=aggregate_fingerprint(sessions, ContextMode.GENERIC),
                recent_states=recent_states[-12:],
            )
            status = result.state
            recent_states.append(status)
            error = None
        except GenerationError as failure:
            status = None
            error = safe_generation_error_code(failure)
            errors += 1
        examples.append(
            {
                "at": at.astimezone(UTC).isoformat(),
                "harness": None,
                "active_agents": count,
                "public_activity": None,
                "status": status,
                "generic_fallback": False,
                "error": error,
            }
        )
    return examples, {
        "admitted": 0,
        "generic_fallbacks": 0,
        "generation_errors": errors,
    }


def ttl_seconds_for_replay() -> int:
    return 1_800


def _aggregate_at(
    timeline: list[dict[str, object]], at: datetime, sample_harness: Harness
) -> tuple[int, list[Harness]]:
    current: dict[str, object] | None = None
    for item in timeline:
        if datetime.fromisoformat(str(item["at"])) <= at:
            current = item
        else:
            break
    active_agents = current.get("active_agents") if current is not None else None
    harness_values = current.get("harnesses") if current is not None else None
    if not isinstance(active_agents, int) or active_agents < 1:
        return 1, [sample_harness]
    harnesses = (
        [Harness(value) for value in harness_values if isinstance(value, str)]
        if isinstance(harness_values, list)
        else []
    )
    return active_agents, harnesses or [sample_harness]


def _parse_codex(
    path: Path, ttl_seconds: int, *, collect_samples: bool = True
) -> tuple[list[_Interval], list[_Sample]] | None:
    records = _read_jsonl(path)
    if records is None:
        return None
    starts: list[datetime] = []
    completions: list[datetime] = []
    samples: list[_Sample] = []
    recognized = False
    for record in records:
        timestamp = _timestamp(record.get("timestamp"))
        if record.get("type") != "event_msg" or timestamp is None:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "task_started":
            starts.append(timestamp)
            recognized = True
        elif kind == "task_complete":
            completions.append(timestamp)
            recognized = True
            source = payload.get("last_agent_message") if collect_samples else None
            if isinstance(source, str) and source.strip():
                samples.append(
                    _Sample(Harness.CODEX, timestamp, _bounded(source.strip()))
                )
    observed = [*starts, *completions]
    if not recognized or not observed:
        return None
    return _contiguous_intervals(Harness.CODEX, observed, ttl_seconds), samples


def _parse_pi(
    path: Path, ttl_seconds: int, *, collect_samples: bool = True
) -> tuple[list[_Interval], list[_Sample]] | None:
    records = _read_jsonl(path)
    if records is None:
        return None
    observed: list[datetime] = []
    samples: list[_Sample] = []
    latest_user: str | None = None
    for record in records:
        timestamp = _timestamp(record.get("timestamp"))
        if timestamp is None or record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        text = _message_text(message.get("content")) if collect_samples else None
        if role == "user":
            observed.append(timestamp)
            if not collect_samples:
                continue
        if role == "user" and text:
            latest_user = text
        elif role == "assistant" and message.get("stopReason") == "stop":
            observed.append(timestamp)
            if not collect_samples or not text:
                continue
            source = f"User: {latest_user}\nAssistant: {text}" if latest_user else text
            samples.append(_Sample(Harness.PI, timestamp, _bounded(source)))
    if not observed:
        return None
    return _contiguous_intervals(Harness.PI, observed, ttl_seconds), samples


def _contiguous_intervals(
    harness: Harness, observed: list[datetime], ttl_seconds: int
) -> list[_Interval]:
    ttl = timedelta(seconds=ttl_seconds)
    intervals: list[_Interval] = []
    for timestamp in sorted(set(observed)):
        if intervals and timestamp <= intervals[-1].ended_at:
            prior = intervals[-1]
            intervals[-1] = _Interval(harness, prior.started_at, timestamp + ttl)
        else:
            intervals.append(_Interval(harness, timestamp, timestamp + ttl))
    return intervals


def _read_jsonl(path: Path) -> list[dict[str, object]] | None:
    try:
        records: list[dict[str, object]] = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    return None
                records.append(payload)
        return records
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _file_could_overlap(path: Path, since: datetime, ttl_seconds: int) -> bool:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except (OSError, OverflowError, ValueError):
        return False
    return modified + timedelta(seconds=ttl_seconds) >= since


def _timestamp(value: object) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
    except (OSError, OverflowError, ValueError):
        return None
    return None


def _message_text(content: object) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts) or None


def _bounded(source: str) -> str:
    return source[-MAX_SOURCE_CHARS:]


def _timeline(intervals: list[_Interval], until: datetime) -> list[dict[str, object]]:
    changes: dict[datetime, list[tuple[Harness, int]]] = {}
    for interval in intervals:
        changes.setdefault(interval.started_at, []).append((interval.harness, 1))
        if interval.ended_at <= until:
            changes.setdefault(interval.ended_at, []).append((interval.harness, -1))
    counts = {Harness.CODEX: 0, Harness.PI: 0}
    timeline: list[dict[str, object]] = []
    for at, updates in sorted(changes.items()):
        for harness, delta in updates:
            counts[harness] += delta
        timeline.append(
            {
                "at": at.astimezone(UTC).isoformat(),
                "active_agents": sum(counts.values()),
                "harnesses": [
                    harness.value for harness in Harness if counts[harness] > 0
                ],
            }
        )
    if timeline and timeline[-1]["at"] != until.astimezone(UTC).isoformat():
        timeline.append(
            {
                "at": until.astimezone(UTC).isoformat(),
                "active_agents": sum(counts.values()),
                "harnesses": [
                    harness.value for harness in Harness if counts[harness] > 0
                ],
            }
        )
    return timeline
