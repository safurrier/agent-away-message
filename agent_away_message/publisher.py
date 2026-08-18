"""Discord RPC boundary; core code remains network-free without an injected client."""

from __future__ import annotations

from collections.abc import Callable
from time import time
from typing import Protocol

from pypresence import ActivityType
from pypresence import Presence as DiscordRpc
from pypresence.exceptions import PyPresenceException

from agent_away_message.models import Presence


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

    def __init__(self, client_id: str) -> None:
        self.rpc = DiscordRpc(client_id)

    def connect(self) -> None:
        try:
            self.rpc.connect()
        except PyPresenceException as error:
            raise ConnectionError("Discord IPC connection failed") from error

    def update(
        self,
        *,
        details: str,
        state: str,
        activity_type: ActivityType,
        name: str,
        start: int,
    ) -> None:
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
        try:
            self.rpc.clear()
        except PyPresenceException as error:
            raise ConnectionError("Discord IPC clear failed") from error

    def close(self) -> None:
        try:
            self.rpc.close()
        except PyPresenceException as error:
            raise ConnectionError("Discord IPC close failed") from error

    def is_connected(self) -> bool:
        writer = self.rpc.sock_writer
        return writer is not None and not writer.is_closing()

    def disconnect(self) -> None:
        writer = self.rpc.sock_writer
        if writer is not None:
            writer.close()
        self.rpc.sock_writer = None
        self.rpc.sock_reader = None


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
