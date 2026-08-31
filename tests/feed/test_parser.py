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
    Trades,
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
    # A channel this build does not model -- `trade` used to be the example here.
    assert isinstance(parse(json.dumps({"channel": "ohlc", "data": []})), Unknown)


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


# -- trade channel ---------------------------------------------------------
#
# The wire shape is transcribed from Kraken's published v2 schema, not observed
# on a socket -- see the note above `_parse_trade`. These tests pin the mapping
# so that when a real capture arrives, a mismatch is one failing assertion
# rather than an archaeology exercise.


def trade_frame(*entries: dict) -> str:
    return json.dumps({"channel": "trade", "type": "update", "data": list(entries)})


def entry(**overrides) -> dict:
    base = {
        "symbol": "BTC/USD",
        "side": "buy",
        "price": 78012.3,
        "qty": 0.015,
        "ord_type": "market",
        "trade_id": 91,
        "timestamp": "2026-08-30T02:24:31.123456Z",
    }
    return base | overrides


def test_trade_frame_keeps_the_wire_digits() -> None:
    """Same reason as the book: a price that went through float is a different price."""
    parsed = parse(trade_frame(entry(price=78012.30, qty=0.10000001)))
    assert isinstance(parsed, Trades)
    trade = parsed.trades[0]
    assert trade.price == Decimal("78012.30")
    assert trade.qty == Decimal("0.10000001")


def test_trade_frame_carries_the_aggressor_side() -> None:
    """The reason for carrying this channel at all: trade sign without inference."""
    assert parse(trade_frame(entry(side="buy"))).trades[0].side == "buy"
    assert parse(trade_frame(entry(side="sell"))).trades[0].side == "sell"


def test_one_frame_can_hold_several_prints() -> None:
    """One taker walking three levels arrives as three trades sharing an instant."""
    parsed = parse(
        trade_frame(
            entry(price=78012.3, trade_id=91),
            entry(price=78012.4, trade_id=92),
            entry(price=78012.5, trade_id=93),
        )
    )
    assert [t.trade_id for t in parsed.trades] == [91, 92, 93]
    assert len({t.exchange_ts_ns for t in parsed.trades}) == 1


def test_trade_timestamp_becomes_nanoseconds() -> None:
    parsed = parse(trade_frame(entry(timestamp="2026-08-30T02:24:31.123456Z")))
    assert parsed.trades[0].exchange_ts_ns == 1788056671123456000


def test_optional_trade_fields_may_be_absent() -> None:
    stripped = entry()
    del stripped["ord_type"]
    del stripped["trade_id"]
    del stripped["timestamp"]
    trade = parse(trade_frame(stripped)).trades[0]
    assert trade.ord_type is None and trade.trade_id is None and trade.exchange_ts_ns is None


def test_a_trade_frame_that_does_not_match_the_schema_is_an_error() -> None:
    """Loudly, and on the first frame -- not as a table that turns out to be empty."""
    for bad in (
        json.dumps({"channel": "trade", "data": []}),
        json.dumps({"channel": "trade", "data": "nope"}),
        json.dumps({"channel": "trade", "data": [{"symbol": "BTC/USD"}]}),
        trade_frame(entry(price="not-a-number")),
    ):
        assert isinstance(parse(bad), ErrorMessage), bad


def test_the_subscribe_backfill_is_marked_as_such() -> None:
    """Kraken replies to a trade subscription with prints that predate the socket.

    On a live probe that was fifty of the first fifty-two, spanning the
    twenty-eight seconds before the recorder started, all arriving at once.
    Treating them as live makes any per-second rate wrong by a factor of fifty.
    """
    assert parse(trade_frame(entry())).is_snapshot is False
    backfill = json.dumps(
        {"channel": "trade", "type": "snapshot", "data": [entry(), entry(trade_id=92)]}
    )
    parsed = parse(backfill)
    assert parsed.is_snapshot is True
    assert len(parsed.trades) == 2
