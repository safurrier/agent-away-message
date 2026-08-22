"""Discord RPC boundary; core code remains network-free without an injected client."""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class DiscordAccount:
    """Public identity returned by a local Discord IPC READY handshake."""

    pipe: int
    user_id: str
    username: str
    display_name: str | None


class DiscordRpcConnection(Protocol):
    """Concrete transport shape used while selecting a Discord account."""

    pipe: int
    ready_payload: dict[str, Any]
    sock_reader: Any
    sock_writer: Any

    def connect(self) -> None: ...

    def update(self, **kwargs: object) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


DiscordRpcFactory = Callable[..., DiscordRpcConnection]


class _ReadyDiscordRpc(DiscordRpc):
    """pypresence connection that retains the READY payload for local selection."""

    ready_payload: dict[str, Any]

    async def handshake(self) -> None:
        ipc_path = get_ipc_path(self.pipe)
        if not ipc_path:
            raise DiscordNotFound

        await self.create_reader_writer(ipc_path)
        self.send_data(0, {"v": 1, "client_id": self.client_id})
        assert self.sock_reader is not None
        preamble = await self.sock_reader.read(8)
        if len(preamble) < 8:
            raise InvalidPipe
        _code, length = struct.unpack("<ii", preamble)
        data = json.loads(await self.sock_reader.read(length))
        if "code" in data:
            if data.get("message") == "Invalid Client ID":
                raise InvalidID
            raise DiscordError(data["code"], data["message"])
        self.ready_payload = data


def _account_from_ready(pipe: int, payload: dict[str, Any]) -> DiscordAccount:
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
        pipe=pipe,
        user_id=user_id,
        username=username,
        display_name=display_name,
    )


def _disconnect_rpc(rpc: DiscordRpcConnection) -> None:
    """Close one transport without closing pypresence's shared event loop."""
    writer = rpc.sock_writer
    if writer is not None:
        writer.close()
    rpc.sock_writer = None
    rpc.sock_reader = None


def _connected_discord_accounts(
    client_id: str,
    rpc_factory: DiscordRpcFactory,
) -> list[tuple[DiscordAccount, DiscordRpcConnection]]:
    connected: list[tuple[DiscordAccount, DiscordRpcConnection]] = []
    for pipe in DISCORD_PIPE_RANGE:
        rpc = rpc_factory(client_id, pipe=pipe)
        try:
            rpc.connect()
            connected.append((_account_from_ready(pipe, rpc.ready_payload), rpc))
        except (DiscordNotFound, InvalidPipe, OSError):
            _disconnect_rpc(rpc)
        except PyPresenceException as error:
            _disconnect_rpc(rpc)
            for _account, open_rpc in connected:
                _disconnect_rpc(open_rpc)
            raise ConnectionError("Discord IPC account discovery failed") from error
        except Exception:
            _disconnect_rpc(rpc)
            for _account, open_rpc in connected:
                _disconnect_rpc(open_rpc)
            raise
    return connected


def discover_discord_accounts(
    client_id: str,
    rpc_factory: DiscordRpcFactory | None = None,
) -> list[DiscordAccount]:
    """List reachable local Discord accounts without publishing a presence."""
    factory = rpc_factory or cast(DiscordRpcFactory, _ReadyDiscordRpc)
    connected = _connected_discord_accounts(client_id, factory)
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
    ) -> None:
        self.client_id = client_id
        self.user_id = user_id
        self.rpc_factory = rpc_factory or cast(DiscordRpcFactory, _ReadyDiscordRpc)
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

        connected = _connected_discord_accounts(self.client_id, self.rpc_factory)
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
