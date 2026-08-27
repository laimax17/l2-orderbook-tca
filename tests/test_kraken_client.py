"""Reconnect state machine, exercised with a fake socket and no real sleeping."""

from __future__ import annotations

import asyncio
import contextlib
import json
import random

import pytest
from tests.conftest import (
    HEARTBEAT_FRAME,
    SNAPSHOT_FRAME,
    STATUS_FRAME,
    UPDATE_FRAME,
    FakeWebSocket,
    sequenced_connect,
)

from l2tca.config import FeedConfig
from l2tca.feed.kraken import KrakenFeedClient, SubscriptionError
from l2tca.feed.messages import BookSnapshot, parse


async def collect(client: KrakenFeedClient, n: int) -> list:
    out = []
    async with contextlib.aclosing(client.stream()) as stream:
        async for message in stream:
            out.append(message)
            if len(out) >= n:
                break
    return out


def recording_sleep() -> tuple[list[float], object]:
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        slept.append(delay)

    return slept, sleep


async def test_subscribe_request_matches_the_v2_schema(config: FeedConfig) -> None:
    socket = FakeWebSocket([STATUS_FRAME, SNAPSHOT_FRAME], on_exhaust="drop")
    client = KrakenFeedClient(
        FeedConfig(symbol="XBT/USD", depth=100, max_reconnects=0),
        connect=sequenced_connect([socket]),
    )
    await collect(client, 3)

    request = next(m for m in socket.sent if m.get("method") == "subscribe")
    assert request["params"] == {
        "channel": "book",
        "symbol": ["BTC/USD"],  # XBT normalised on the wire
        "depth": 100,
        "snapshot": True,
    }


async def test_handshake_frames_are_not_swallowed(config: FeedConfig) -> None:
    """The ack and anything before it must land in the recording, snapshot included."""
    socket = FakeWebSocket([STATUS_FRAME, SNAPSHOT_FRAME, UPDATE_FRAME], on_exhaust="hang")
    client = KrakenFeedClient(config, connect=sequenced_connect([socket]))

    messages = await collect(client, 4)
    kinds = [type(parse(m.payload)).__name__ for m in messages]
    assert "SubscriptionAck" in kinds
    assert "Status" in kinds
    assert "BookSnapshot" in kinds
    assert [m.seq for m in messages] == [0, 1, 2, 3]
    await client.aclose()


async def test_snapshot_before_ack_still_completes_the_handshake(config: FeedConfig) -> None:
    socket = FakeWebSocket([SNAPSHOT_FRAME], on_exhaust="hang", ack=False)
    client = KrakenFeedClient(config, connect=sequenced_connect([socket]))
    messages = await collect(client, 1)
    assert isinstance(parse(messages[0].payload), BookSnapshot)
    await client.aclose()


async def test_rejected_subscription_is_not_retried(config: FeedConfig) -> None:
    rejection = json.dumps(
        {"method": "subscribe", "req_id": 1, "success": False, "error": "Unsupported depth"}
    )
    socket = FakeWebSocket([rejection], on_exhaust="hang", ack=False)
    client = KrakenFeedClient(config, connect=sequenced_connect([socket]))

    with pytest.raises(SubscriptionError, match="Unsupported depth"):
        await collect(client, 1)
    assert socket.closed


async def test_dropped_connection_reconnects_and_resubscribes(config: FeedConfig) -> None:
    first = FakeWebSocket([SNAPSHOT_FRAME], on_exhaust="drop")
    second = FakeWebSocket([UPDATE_FRAME, HEARTBEAT_FRAME], on_exhaust="hang")
    slept, sleep = recording_sleep()

    client = KrakenFeedClient(
        config,
        connect=sequenced_connect([first, second]),
        sleep=sleep,
        rng=random.Random(1),
    )
    messages = await collect(client, 5)
    await client.aclose()

    assert client.stats.connects == 2
    assert client.stats.reconnects == 1
    assert len(slept) == 1 and 0 <= slept[0] <= config.backoff_initial_s
    assert first.closed
    assert any(m.get("method") == "subscribe" for m in second.sent), "must resubscribe"
    # seq is continuous across the reconnect, so a gap in a capture is visible.
    assert [m.seq for m in messages] == list(range(len(messages)))


async def test_backoff_grows_across_consecutive_failures(config: FeedConfig) -> None:
    """Sockets that never deliver data must not reset the ladder."""
    sockets = [FakeWebSocket([], on_exhaust="drop", ack=False) for _ in range(4)]
    slept, sleep = recording_sleep()
    client = KrakenFeedClient(
        FeedConfig(
            backoff_initial_s=1.0,
            backoff_max_s=64.0,
            backoff_multiplier=2.0,
            backoff_jitter=False,
            max_reconnects=3,
        ),
        connect=sequenced_connect(sockets),
        sleep=sleep,
    )
    await collect(client, 1)
    assert slept == [1.0, 2.0, 4.0]


async def test_a_healthy_session_resets_the_backoff_ladder(config: FeedConfig) -> None:
    healthy = [FakeWebSocket([SNAPSHOT_FRAME, UPDATE_FRAME], on_exhaust="drop") for _ in range(3)]
    slept, sleep = recording_sleep()
    client = KrakenFeedClient(
        FeedConfig(backoff_initial_s=1.0, backoff_max_s=64.0, backoff_jitter=False),
        connect=sequenced_connect(healthy),
        sleep=sleep,
    )
    await collect(client, 9)
    await client.aclose()
    assert slept == [1.0, 1.0], "each productive session should restart the ladder"


async def test_max_reconnects_gives_up(config: FeedConfig) -> None:
    sockets = [FakeWebSocket([], on_exhaust="drop", ack=False) for _ in range(5)]
    _slept, sleep = recording_sleep()
    client = KrakenFeedClient(
        FeedConfig(max_reconnects=2, backoff_jitter=False),
        connect=sequenced_connect(sockets),
        sleep=sleep,
    )
    messages = await collect(client, 100)
    assert messages == []
    assert any(e.event == "giving_up" for e in client.stats.control)


async def test_silence_triggers_a_ping_then_a_reconnect() -> None:
    """A half-open connection looks exactly like an idle one until you write to it."""
    quiet = FakeWebSocket([SNAPSHOT_FRAME], on_exhaust="hang")
    replacement = FakeWebSocket([UPDATE_FRAME], on_exhaust="hang")
    _slept, sleep = recording_sleep()

    client = KrakenFeedClient(
        FeedConfig(stale_after_s=0.05, backoff_jitter=False),
        connect=sequenced_connect([quiet, replacement]),
        sleep=sleep,
    )
    messages = await collect(client, 3)
    await client.aclose()

    assert any(m.get("method") == "ping" for m in quiet.sent), "should probe before giving up"
    assert client.stats.stale_timeouts >= 2
    assert client.stats.reconnects == 1
    assert len(messages) == 3


async def test_aclose_stops_the_stream_and_closes_the_socket(config: FeedConfig) -> None:
    socket = FakeWebSocket([SNAPSHOT_FRAME], on_exhaust="hang")
    client = KrakenFeedClient(config, connect=sequenced_connect([socket]))

    stream = client.stream()
    async with contextlib.aclosing(stream):
        await anext(stream)  # the ack
        await client.aclose()
    assert socket.closed


async def test_control_events_describe_the_lifecycle(config: FeedConfig) -> None:
    seen = []
    first = FakeWebSocket([SNAPSHOT_FRAME], on_exhaust="drop")
    second = FakeWebSocket([UPDATE_FRAME], on_exhaust="hang")
    _slept, sleep = recording_sleep()
    client = KrakenFeedClient(
        config,
        connect=sequenced_connect([first, second]),
        sleep=sleep,
        on_control=seen.append,
    )
    await collect(client, 4)
    await client.aclose()

    events = [e.event for e in seen]
    assert events[:2] == ["connected", "subscribed"]
    assert "disconnected" in events
    assert "reconnect" in events
    assert "resubscribed" in events


async def test_cancellation_propagates(config: FeedConfig) -> None:
    socket = FakeWebSocket([SNAPSHOT_FRAME], on_exhaust="hang")
    client = KrakenFeedClient(config, connect=sequenced_connect([socket]))

    async def run() -> None:
        async with contextlib.aclosing(client.stream()) as stream:
            async for _ in stream:
                pass

    task = asyncio.create_task(run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
