"""Ephemeral daemon-owned contextual work; raw input never reaches disk."""

from __future__ import annotations

import json
import re
import socket
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Final

MAX_CONTEXT_CHARS: Final = 6_000
MAX_CONTEXT_PACKET_BYTES: Final = 1_900
_TOKEN: Final = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True)
class ContextJob:
    session_id: str
    turn_token: str
    source: str
    phase: str = "provisional"


class ContextQueue:
    """A bounded queue where settled work supersedes provisional work per turn."""

    def __init__(self, limit: int = 16) -> None:
        self._jobs: deque[ContextJob] = deque(maxlen=limit)
        self._limit = limit
        self._settled_rejections: deque[tuple[str, str]] = deque(maxlen=limit)
        self._lock = Lock()

    def submit(self, job: ContextJob) -> bool:
        if (
            not job.source
            or len(job.source) > MAX_CONTEXT_CHARS
            or not _TOKEN.fullmatch(job.session_id)
            or not _TOKEN.fullmatch(job.turn_token)
            or job.phase not in {"provisional", "settled"}
        ):
            return False
        with self._lock:
            key = (job.session_id, job.turn_token)
            if job.phase == "provisional" and key in self._settled_rejections:
                return False

            def same_turn(item: ContextJob) -> bool:
                return (
                    item.session_id == job.session_id
                    and item.turn_token == job.turn_token
                )

            existing = [item for item in self._jobs if same_turn(item)]
            if job.phase == "provisional" and any(
                item.phase == "settled" for item in existing
            ):
                return False
            # New work for one turn replaces stale provisional work. A settled
            # completion is never displaced by a late input delivery.
            self._jobs = deque(
                (item for item in self._jobs if not same_turn(item)), maxlen=self._limit
            )
            if len(self._jobs) >= self._limit:
                if job.phase != "settled":
                    return False
                provisional = next(
                    (item for item in self._jobs if item.phase == "provisional"), None
                )
                if provisional is None:
                    return False
                self._jobs.remove(provisional)
            self._jobs.append(job)
        return True

    def suppress_provisional(self, session_id: str, turn_token: str) -> None:
        """Boundedly remember a settled rejection so late input cannot resurrect it."""
        key = (session_id, turn_token)
        if not _TOKEN.fullmatch(session_id) or not _TOKEN.fullmatch(turn_token):
            return
        with self._lock:
            if key not in self._settled_rejections:
                self._settled_rejections.append(key)
            self._jobs = deque(
                (
                    item
                    for item in self._jobs
                    if (item.session_id, item.turn_token) != key
                    or item.phase != "provisional"
                ),
                maxlen=self._limit,
            )

    def take(self) -> ContextJob | None:
        with self._lock:
            return self._jobs.popleft() if self._jobs else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)


def deliver_context(
    socket_path: Path,
    session_id: str,
    turn_token: str,
    source: str,
    phase: str = "provisional",
) -> bool:
    """Best-effort bounded one-shot delivery to a local daemon datagram socket."""
    if (
        not source
        or len(source) > MAX_CONTEXT_CHARS
        or not _TOKEN.fullmatch(session_id)
        or not _TOKEN.fullmatch(turn_token)
        or phase not in {"provisional", "settled"}
    ):
        return False
    payload = _encode_context(session_id, turn_token, source, phase)
    if len(payload) > MAX_CONTEXT_PACKET_BYTES:
        low = 1
        high = len(source)
        fitted = b""
        while low <= high:
            width = (low + high) // 2
            candidate = _encode_context(session_id, turn_token, source[-width:], phase)
            if len(candidate) <= MAX_CONTEXT_PACKET_BYTES:
                fitted = candidate
                low = width + 1
            else:
                high = width - 1
        if not fitted:
            return False
        payload = fitted
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        client.setblocking(False)
        client.sendto(payload, str(socket_path))
        return True
    except OSError:
        return False
    finally:
        client.close()


def _encode_context(session_id: str, turn_token: str, source: str, phase: str) -> bytes:
    return json.dumps(
        {
            "session_id": session_id,
            "turn_token": turn_token,
            "source": source,
            "phase": phase,
        }
    ).encode()


def receive_context(packet: bytes) -> ContextJob | None:
    """Decode only a bounded message; malformed packets are discarded silently."""
    if len(packet) > MAX_CONTEXT_PACKET_BYTES:
        return None
    try:
        payload = json.loads(packet)
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("session_id")
        turn_token = payload.get("turn_token")
        source = payload.get("source")
        phase = payload.get("phase", "provisional")
        if (
            not isinstance(session_id, str)
            or not isinstance(turn_token, str)
            or not isinstance(source, str)
            or len(source) > MAX_CONTEXT_CHARS
            or not isinstance(phase, str)
            or phase not in {"provisional", "settled"}
        ):
            return None
        job = ContextJob(session_id, turn_token, source, phase)
        return (
            job
            if _TOKEN.fullmatch(session_id) and _TOKEN.fullmatch(turn_token)
            else None
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def process_one(
    queue: ContextQueue,
    summarize: Callable[[str], str | None],
    save: Callable[[str, str, str, str], None],
    reject: Callable[[str, str, str], None] | None = None,
) -> bool:
    """Process one job; semantic rejection is distinct from backend failure."""
    job = queue.take()
    if job is None:
        return False
    activity = summarize(job.source)
    if activity is not None:
        save(job.session_id, job.turn_token, activity, job.phase)
    elif reject is not None:
        reject(job.session_id, job.turn_token, job.phase)
    return True
