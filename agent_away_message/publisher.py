"""Discord RPC boundary; core code remains network-free without an injected client."""

from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Protocol, cast

from pypresence import ActivityType
from pypresence import Presence as DiscordRpc
from pypresence.exceptions import (
    DiscordError,
    DiscordNotFound,
    InvalidID,
    InvalidPipe,
    PyPresenceException,
)
from pypresence.utils import get_ipc_path

from agent_away_message.models import Presence

# Discord's local RPC contract reserves these ten numbered endpoints.
DISCORD_PIPE_RANGE = range(10)
DISCORD_READY_MAX_BYTES = 1024 * 1024
_DISCORD_PIPE_NAME = re.compile(r"^discord-ipc-([0-9])$")


@dataclass(frozen=True)
class DiscordEndpoint:
    """One exact local Discord IPC endpoint."""

    pipe: int
    path: str


@dataclass(frozen=True)
class DiscordAccount:
    """Public identity returned by a local Discord IPC READY handshake."""

    pipe: int
    user_id: str
    username: str
    display_name: str | None


class DiscordRpcConnection(Protocol):
    """Concrete transport shape used while selecting a Discord account."""

    endpoint: DiscordEndpoint
    ready_payload: dict[str, Any]
    sock_reader: Any
    sock_writer: Any

    def connect(self) -> None: ...

    def update(self, **kwargs: object) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


DiscordRpcFactory = Callable[..., DiscordRpcConnection]
DiscordEndpointProvider = Callable[[], list[DiscordEndpoint]]


def _discord_endpoints() -> list[DiscordEndpoint]:
    """Enumerate every exact IPC endpoint instead of selecting one path per pipe."""
    if sys.platform == "win32":
        endpoints = []
        for pipe in DISCORD_PIPE_RANGE:
            path = get_ipc_path(pipe)
            if path:
                endpoints.append(DiscordEndpoint(pipe, path))
        return endpoints
    if sys.platform not in {"linux", "darwin"}:
        return []

    user_runtime = Path(f"/run/user/{os.getuid()}")
    tempdir = Path(
        os.environ.get("XDG_RUNTIME_DIR")
        or (user_runtime if user_runtime.exists() else tempfile.gettempdir())
    )
    roots = [
        ".",
        "..",
        "snap.discord",
        "app/com.discordapp.Discord",
        "app/com.discordapp.DiscordCanary",
    ]
    endpoints: list[DiscordEndpoint] = []
    seen: set[str] = set()
    for relative in roots:
        root = (tempdir / relative).resolve()
        if not root.is_dir():
            continue
        try:
            entries = sorted(os.scandir(root), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            match = _DISCORD_PIPE_NAME.fullmatch(entry.name)
            if match is None:
                continue
            resolved = str(Path(entry.path).resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            endpoints.append(DiscordEndpoint(int(match.group(1)), entry.path))
    return sorted(endpoints, key=lambda endpoint: (endpoint.pipe, endpoint.path))


class _ReadyDiscordRpc(DiscordRpc):
    """pypresence connection that retains the READY payload for local selection."""

    ready_payload: dict[str, Any]

    def __init__(self, client_id: str, *, endpoint: DiscordEndpoint) -> None:
        super().__init__(client_id, pipe=endpoint.pipe)
        self.endpoint = endpoint

    async def handshake(self) -> None:
        await self.create_reader_writer(self.endpoint.path)
        self.send_data(0, {"v": 1, "client_id": self.client_id})
        assert self.sock_reader is not None
        try:
            preamble = await asyncio.wait_for(
                self.sock_reader.readexactly(8), self.response_timeout
            )
            opcode, length = struct.unpack("<II", preamble)
            if opcode != 1 or length > DISCORD_READY_MAX_BYTES:
                raise InvalidPipe
            raw = await asyncio.wait_for(
                self.sock_reader.readexactly(length), self.response_timeout
            )
            data = json.loads(raw)
        except (
            asyncio.IncompleteReadError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            struct.error,
        ) as error:
            raise InvalidPipe from error
        if not isinstance(data, dict):
            raise InvalidPipe
        if "code" in data:
            if data.get("message") == "Invalid Client ID":
                raise InvalidID
            raise DiscordError(data["code"], data["message"])
        if data.get("cmd") != "DISPATCH" or data.get("evt") != "READY":
            raise InvalidPipe
        self.ready_payload = data


def _account_from_ready(
    endpoint: DiscordEndpoint, payload: dict[str, Any]
) -> DiscordAccount:
    try:
        user = payload["data"]["user"]
        user_id = user["id"]
        username = user["username"]
        display_name = user.get("global_name")
    except (KeyError, TypeError) as error:
        raise ConnectionError("Discord IPC READY response was invalid") from error
    if not isinstance(user_id, str) or not isinstance(username, str):
        raise ConnectionError("Discord IPC READY response was invalid")
    if display_name is not None and not isinstance(display_name, str):
        raise ConnectionError("Discord IPC READY response was invalid")
    return DiscordAccount(
        pipe=endpoint.pipe,
        user_id=user_id,
        username=username,
        display_name=display_name,
    )


def _disconnect_rpc(rpc: DiscordRpcConnection) -> None:
    """Close a probe transport and its pypresence-owned event loop."""
    writer = rpc.sock_writer
    loop = getattr(rpc, "loop", None)
    try:
        if writer is not None:
            rpc.close()
    except Exception:
        # Cleanup must not let a broken endpoint hide another valid account.
        if writer is not None:
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass
    finally:
        if loop is not None and not loop.is_closed():
            loop.close()
        rpc.sock_writer = None
        rpc.sock_reader = None


def _connected_discord_accounts(
    client_id: str,
    rpc_factory: DiscordRpcFactory,
    endpoint_provider: DiscordEndpointProvider,
) -> list[tuple[DiscordAccount, DiscordRpcConnection]]:
    connected: list[tuple[DiscordAccount, DiscordRpcConnection]] = []
    for endpoint in endpoint_provider():
        rpc = rpc_factory(client_id, endpoint=endpoint)
        try:
            rpc.connect()
            connected.append((_account_from_ready(endpoint, rpc.ready_payload), rpc))
        except InvalidID as error:
            _disconnect_rpc(rpc)
            for _account, open_rpc in connected:
                _disconnect_rpc(open_rpc)
            raise ConnectionError("Discord application client ID is invalid") from error
        except (
            DiscordNotFound,
            DiscordError,
            InvalidPipe,
            PyPresenceException,
            ConnectionError,
            OSError,
        ):
            # One stale or malformed endpoint must not hide a later valid account.
            _disconnect_rpc(rpc)
    return connected


def discover_discord_accounts(
    client_id: str,
    rpc_factory: DiscordRpcFactory | None = None,
    endpoint_provider: DiscordEndpointProvider | None = None,
) -> list[DiscordAccount]:
    """List reachable local Discord accounts without publishing a presence."""
    factory = rpc_factory or cast(DiscordRpcFactory, _ReadyDiscordRpc)
    connected = _connected_discord_accounts(
        client_id, factory, endpoint_provider or _discord_endpoints
    )
    try:
        return [account for account, _rpc in connected]
    finally:
        for _account, rpc in connected:
            _disconnect_rpc(rpc)


class PresenceClient(Protocol):
    """Subset of Discord RPC used by this application."""

    def connect(self) -> None: ...

    def update(
        self,
        *,
        details: str,
        state: str,
        activity_type: ActivityType,
        name: str,
        start: int,
    ) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...

    def is_connected(self) -> bool: ...

    def disconnect(self) -> None: ...


class PypresenceClient:
    """Concrete pypresence adapter kept behind the core publisher protocol."""

    def __init__(
        self,
        client_id: str,
        user_id: str | None = None,
        rpc_factory: DiscordRpcFactory | None = None,
        endpoint_provider: DiscordEndpointProvider | None = None,
    ) -> None:
        self.client_id = client_id
        self.user_id = user_id
        self.rpc_factory = rpc_factory or cast(DiscordRpcFactory, _ReadyDiscordRpc)
        self.endpoint_provider = endpoint_provider or _discord_endpoints
        self.rpc: DiscordRpcConnection | None = (
            cast(DiscordRpcConnection, DiscordRpc(client_id))
            if user_id is None
            else None
        )

    def connect(self) -> None:
        if self.user_id is None:
            assert self.rpc is not None
            try:
                self.rpc.connect()
            except PyPresenceException as error:
                raise ConnectionError("Discord IPC connection failed") from error
            return

        connected = _connected_discord_accounts(
            self.client_id, self.rpc_factory, self.endpoint_provider
        )
        selected: DiscordRpcConnection | None = None
        for account, rpc in connected:
            if selected is None and account.user_id == self.user_id:
                selected = rpc
            else:
                _disconnect_rpc(rpc)
        if selected is None:
            raise ConnectionError("configured Discord account is not available")
        self.rpc = selected

    def update(
        self,
        *,
        details: str,
        state: str,
        activity_type: ActivityType,
        name: str,
        start: int,
    ) -> None:
        assert self.rpc is not None
        try:
            self.rpc.update(
                details=details,
                state=state,
                activity_type=activity_type,
                name=name,
                start=start,
            )
        except PyPresenceException as error:
            raise ConnectionError("Discord IPC update failed") from error

    def clear(self) -> None:
        assert self.rpc is not None
        try:
            self.rpc.clear()
        except PyPresenceException as error:
            raise ConnectionError("Discord IPC clear failed") from error

    def close(self) -> None:
        if self.rpc is None:
            return
        try:
            self.rpc.close()
        except PyPresenceException as error:
            raise ConnectionError("Discord IPC close failed") from error
        finally:
            self.rpc = None

    def is_connected(self) -> bool:
        if self.rpc is None:
            return False
        writer = self.rpc.sock_writer
        return writer is not None and not writer.is_closing()

    def disconnect(self) -> None:
        if self.rpc is None:
            return
        _disconnect_rpc(self.rpc)
        if self.user_id is not None:
            self.rpc = None


class Publisher(Protocol):
    """Narrow output seam for preview and Discord publication."""

    def publish(self, presence: Presence | None) -> None: ...

    def close(self) -> None: ...


class MemoryPublisher:
    """In-process publisher used for preview tests and dry runs."""

    def __init__(self) -> None:
        self.last: Presence | None = None

    def publish(self, presence: Presence | None) -> None:
        self.last = presence

    def close(self) -> None:
        return None


class DiscordPublisher:
    """Reconnect-once wrapper around an injected Discord RPC client."""

    def __init__(
        self, client: PresenceClient, clock: Callable[[], float] = time
    ) -> None:
        self.client = client
        self.clock = clock
        self.connected = False
        self.last_published: Presence | None = None
        self.started_at: int | None = None

    def publish(self, presence: Presence | None) -> None:
        if presence is None:
            try:
                if self.connected and self.last_published is not None:
                    self.client.clear()
            except (ConnectionError, OSError):
                self._disconnect()
                raise
            finally:
                self.last_published = None
                self.started_at = None
            return
        if self.connected and presence == self.last_published:
            if self.client.is_connected():
                return
            self._disconnect()
        self._update(presence, retry=True)

    def close(self) -> None:
        try:
            if self.connected:
                try:
                    if self.last_published is not None:
                        self.client.clear()
                finally:
                    self.client.close()
        finally:
            self.connected = False
            self.last_published = None
            self.started_at = None

    def _update(self, presence: Presence, retry: bool) -> None:
        try:
            if not self.connected:
                self.client.connect()
                self.connected = True
            if self.started_at is None:
                self.started_at = int(self.clock())
            self.client.update(
                details=presence.details,
                state=presence.state,
                activity_type=ActivityType.WATCHING,
                name="agents at work",
                start=self.started_at,
            )
            self.last_published = presence
        except (ConnectionError, OSError):
            self._disconnect()
            if retry:
                self._update(presence, retry=False)
            else:
                raise

    def _disconnect(self) -> None:
        try:
            self.client.disconnect()
        except (ConnectionError, OSError):
            pass
        finally:
            self.connected = False
