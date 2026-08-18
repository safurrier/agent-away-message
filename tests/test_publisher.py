from __future__ import annotations

import pytest
from pypresence import ActivityType

from agent_away_message.models import Presence
from agent_away_message.publisher import DiscordPublisher


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
