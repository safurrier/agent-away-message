from __future__ import annotations

import pytest
from pypresence import ActivityType

from agent_away_message.models import Presence
from agent_away_message.publisher import (
    DiscordPublisher,
    PypresenceClient,
    discover_discord_accounts,
)


class FakeRpc:
    def __init__(self) -> None:
        self.connects = 0
        self.updates = 0
        self.clears = 0
        self.fail_first_update = True
        self.always_fail_update = False
        self.fail_clear = False
        self.payloads: list[dict[str, object]] = []
        self.healthy = True
        self.disconnects = 0

    def connect(self) -> None:
        self.connects += 1

    def update(
        self,
        *,
        details: str,
        state: str,
        activity_type: ActivityType,
        name: str,
        start: int,
    ) -> None:
        self.updates += 1
        self.payloads.append(
            {
                "details": details,
                "state": state,
                "activity_type": activity_type,
                "name": name,
                "start": start,
            }
        )
        if self.fail_first_update or self.always_fail_update:
            self.fail_first_update = False
            raise ConnectionError("fake disconnect")

    def clear(self) -> None:
        self.clears += 1
        if self.fail_clear:
            raise ConnectionError("fake clear disconnect")

    def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return self.healthy

    def disconnect(self) -> None:
        self.disconnects += 1


def test_discord_publisher_reconnects_once_and_clears() -> None:
    rpc = FakeRpc()
    publisher = DiscordPublisher(rpc, clock=lambda: 1_700_000_000)

    publisher.publish(Presence(details="1 coding agent active", state="waiting"))
    publisher.publish(None)

    assert rpc.connects == 2
    assert rpc.updates == 2
    assert rpc.disconnects == 1
    assert rpc.clears == 1
    assert rpc.payloads[-1]["activity_type"] is ActivityType.WATCHING
    assert rpc.payloads[-1]["name"] == "agents at work"
    assert rpc.payloads[-1]["start"] == 1_700_000_000


def test_failed_updates_disconnect_each_open_transport() -> None:
    rpc = FakeRpc()
    rpc.always_fail_update = True
    publisher = DiscordPublisher(rpc)

    with pytest.raises(ConnectionError):
        publisher.publish(Presence(details="1 coding agent active", state="testing"))

    assert rpc.connects == 2
    assert rpc.disconnects == 2
    assert publisher.connected is False


def test_discord_publisher_suppresses_duplicate_successful_payloads() -> None:
    rpc = FakeRpc()
    rpc.fail_first_update = False
    publisher = DiscordPublisher(rpc)
    presence = Presence(details="18 coding agents active", state="testing")

    publisher.publish(presence)
    publisher.publish(presence)
    publisher.publish(None)
    publisher.publish(None)

    assert rpc.updates == 1
    assert rpc.clears == 1


def test_discord_publisher_close_clears_presence_and_socket() -> None:
    rpc = FakeRpc()
    rpc.fail_first_update = False
    publisher = DiscordPublisher(rpc)

    publisher.publish(Presence(details="1 coding agent active", state="testing"))
    publisher.close()

    assert rpc.clears == 1
    assert rpc.closed is True
    assert publisher.connected is False


def test_duplicate_presence_reconnects_when_transport_is_closed() -> None:
    rpc = FakeRpc()
    rpc.fail_first_update = False
    publisher = DiscordPublisher(rpc)
    presence = Presence(details="18 coding agents active", state="testing")
    publisher.publish(presence)
    rpc.healthy = False

    publisher.publish(presence)

    assert rpc.connects == 2
    assert rpc.updates == 2


def test_elapsed_timer_survives_status_changes_and_reconnects() -> None:
    rpc = FakeRpc()
    rpc.fail_first_update = False
    times = iter([1_700_000_000, 1_700_000_999])
    publisher = DiscordPublisher(rpc, clock=lambda: next(times))

    publisher.publish(Presence(details="2 coding agents active", state="first"))
    publisher.publish(Presence(details="3 coding agents active", state="second"))
    rpc.healthy = False
    publisher.publish(Presence(details="3 coding agents active", state="second"))

    assert [payload["start"] for payload in rpc.payloads] == [
        1_700_000_000,
        1_700_000_000,
        1_700_000_000,
    ]


def test_elapsed_timer_resets_after_presence_clears() -> None:
    rpc = FakeRpc()
    rpc.fail_first_update = False
    times = iter([1_700_000_000, 1_700_000_999])
    publisher = DiscordPublisher(rpc, clock=lambda: next(times))

    publisher.publish(Presence(details="1 coding agent active", state="first"))
    publisher.publish(None)
    publisher.publish(Presence(details="1 coding agent active", state="second"))

    assert [payload["start"] for payload in rpc.payloads] == [
        1_700_000_000,
        1_700_000_999,
    ]


def test_failed_clear_disconnects_the_transport() -> None:
    rpc = FakeRpc()
    rpc.fail_first_update = False
    publisher = DiscordPublisher(rpc)
    publisher.publish(Presence(details="1 coding agent active", state="testing"))
    rpc.fail_clear = True

    with pytest.raises(ConnectionError):
        publisher.publish(None)

    assert rpc.disconnects == 1
    assert publisher.connected is False


class FakeWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


class FakeAccountRpc:
    def __init__(self, pipe: int, identity: tuple[str, str, str | None] | None) -> None:
        self.pipe = pipe
        self.identity = identity
        self.sock_reader = object()
        self.writer = FakeWriter()
        self.sock_writer: FakeWriter | None = self.writer
        self.updates = 0
        self.ready_payload: dict[str, object] = {}

    def connect(self) -> None:
        if self.identity is None:
            from pypresence.exceptions import DiscordNotFound

            raise DiscordNotFound
        user_id, username, display_name = self.identity
        self.ready_payload = {
            "data": {
                "user": {
                    "id": user_id,
                    "username": username,
                    "global_name": display_name,
                }
            }
        }

    def update(self, **_kwargs: object) -> None:
        self.updates += 1

    def clear(self) -> None:
        return None

    def close(self) -> None:
        if self.sock_writer is not None:
            self.sock_writer.close()


class FakeAccountRpcFactory:
    def __init__(self, identities: dict[int, tuple[str, str, str | None]]) -> None:
        self.identities = identities
        self.created: list[FakeAccountRpc] = []

    def __call__(self, _client_id: str, *, pipe: int) -> FakeAccountRpc:
        rpc = FakeAccountRpc(pipe, self.identities.get(pipe))
        self.created.append(rpc)
        return rpc


def test_discover_discord_accounts_is_ordered_and_closes_every_probe() -> None:
    factory = FakeAccountRpcFactory(
        {
            4: ("work-id", "alex.f", "alex"),
            1: ("personal-id", "__chef__", "Chef"),
        }
    )

    accounts = discover_discord_accounts("application-id", factory)

    assert [(account.pipe, account.user_id) for account in accounts] == [
        (1, "personal-id"),
        (4, "work-id"),
    ]
    assert all(rpc.writer.closed for rpc in factory.created)
    assert all(rpc.updates == 0 for rpc in factory.created)


def test_account_selector_chooses_user_id_not_first_pipe() -> None:
    factory = FakeAccountRpcFactory(
        {
            0: ("personal-id", "__chef__", "Chef"),
            1: ("work-id", "alex.f", "alex"),
        }
    )
    client = PypresenceClient("application-id", "work-id", factory)

    client.connect()

    assert client.rpc is not None
    assert client.rpc.pipe == 1
    personal = next(rpc for rpc in factory.created if rpc.pipe == 0)
    selected = next(rpc for rpc in factory.created if rpc.pipe == 1)
    assert personal.writer.closed is True
    assert selected.writer.closed is False


def test_account_selector_fails_closed_when_user_is_absent() -> None:
    factory = FakeAccountRpcFactory({0: ("personal-id", "__chef__", "Chef")})
    client = PypresenceClient("application-id", "work-id", factory)

    with pytest.raises(ConnectionError, match="configured Discord account"):
        client.connect()

    assert all(rpc.writer.closed for rpc in factory.created)


def test_account_selector_rediscovers_after_disconnect() -> None:
    factory = FakeAccountRpcFactory({1: ("work-id", "alex.f", "alex")})
    client = PypresenceClient("application-id", "work-id", factory)
    client.connect()
    assert client.rpc is not None
    assert client.rpc.pipe == 1

    client.disconnect()
    factory.identities = {0: ("work-id", "alex.f", "alex")}
    client.connect()

    assert client.rpc is not None
    assert client.rpc.pipe == 0
