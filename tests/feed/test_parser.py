from __future__ import annotations

import json
from decimal import Decimal

from tests.conftest import HEARTBEAT_FRAME, SNAPSHOT_FRAME, STATUS_FRAME, UPDATE_FRAME

from l2tca.feed.messages import (
    BookSnapshot,
    BookUpdate,
    ErrorMessage,
    Heartbeat,
    Pong,
    RawMessage,
    Status,
    SubscriptionAck,
    Unknown,
)
from l2tca.feed.parser import parse, parse_exchange_timestamp


def test_snapshot_parses_levels_and_checksum() -> None:
    parsed = parse(SNAPSHOT_FRAME)
    assert isinstance(parsed, BookSnapshot)
    assert parsed.symbol == "BTC/USD"
    assert len(parsed.bids) == 2
    assert parsed.bids[0].price == Decimal("45283.5")
    assert parsed.checksum == 3070994277
    assert parsed.exchange_ts_ns is None


def test_prices_keep_their_exact_decimal_digits() -> None:
    """The checksum is computed over the digits Kraken sent, so they must survive."""
    frame = json.dumps(
        {
            "channel": "book",
            "type": "update",
            "data": [{"symbol": "BTC/USD", "bids": [{"price": 0.1, "qty": 0.3}], "asks": []}],
        }
    )
    parsed = parse(frame)
    assert isinstance(parsed, BookUpdate)
    assert str(parsed.bids[0].price) == "0.1"
    assert parsed.bids[0].price + Decimal("0.2") == Decimal("0.3")


def test_update_carries_zero_quantity_deletes_and_a_timestamp() -> None:
    parsed = parse(UPDATE_FRAME)
    assert isinstance(parsed, BookUpdate)
    assert parsed.bids[0].qty == Decimal(0)
    assert parsed.exchange_ts_ns is not None
    assert parsed.exchange_ts_ns % 1000 == 0  # microsecond resolution, ns units


def test_heartbeat_and_status() -> None:
    assert isinstance(parse(HEARTBEAT_FRAME), Heartbeat)
    status = parse(STATUS_FRAME)
    assert isinstance(status, Status)
    assert status.system == "online"
    assert status.connection_id == 12345


def test_a_rejected_subscription_is_still_an_ack() -> None:
    """Turning it into a generic error would leave the handshake waiting forever."""
    bad = parse(
        json.dumps(
            {"method": "subscribe", "req_id": 4, "success": False, "error": "Subscription failed"}
        )
    )
    assert isinstance(bad, SubscriptionAck)
    assert not bad.success
    assert bad.req_id == 4
    assert bad.error == "Subscription failed"


def test_successful_subscription_ack() -> None:
    ok = parse(json.dumps({"method": "subscribe", "req_id": 3, "success": True, "result": {}}))
    assert isinstance(ok, SubscriptionAck)
    assert ok.success and ok.req_id == 3


def test_a_bare_error_frame_is_an_error() -> None:
    bare = parse(json.dumps({"error": "Malformed request"}))
    assert isinstance(bare, ErrorMessage)
    assert "Malformed request" in bare.error


def test_pong_and_unknown_channel() -> None:
    assert isinstance(parse(json.dumps({"method": "pong", "req_id": 9})), Pong)
    assert isinstance(parse(json.dumps({"channel": "trade", "data": []})), Unknown)


def test_parse_never_raises_on_garbage() -> None:
    """The feed has to survive whatever lands on the wire."""
    for payload in (
        "",
        "{",
        "null",
        "[]",
        '{"channel":"book","type":"update"}',
        '{"channel":"book","type":"update","data":[{}]}',
        '{"channel":"book","type":"wat","data":[{"symbol":"X"}]}',
    ):
        assert isinstance(parse(payload), ErrorMessage | Unknown)


def test_parse_exchange_timestamp_tolerates_junk() -> None:
    assert parse_exchange_timestamp(None) is None
    assert parse_exchange_timestamp("not a date") is None
    assert parse_exchange_timestamp("2026-01-02T09:00:01.440295Z") > 0


def test_raw_message_stamps_both_clocks() -> None:
    message = RawMessage.stamp("{}", seq=7)
    assert message.seq == 7
    assert message.recv_ns > 0
    assert message.recv_wall_ns > 1_600_000_000_000_000_000
