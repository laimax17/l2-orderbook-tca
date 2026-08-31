"""Decoding of Kraken v2 frames into the types in :mod:`l2tca.feed.messages`.

:func:`parse` never raises. The feed has to survive anything the exchange puts
on the wire, so every failure is a value (:class:`ErrorMessage`), not an
exception. That is a deliberate exception to this project's "let it crash"
default: a malformed frame is an expected input here, not a bug.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from l2tca.feed.messages import (
    BookLevel,
    BookSnapshot,
    BookUpdate,
    ErrorMessage,
    Heartbeat,
    ParsedMessage,
    Pong,
    Status,
    SubscriptionAck,
    Trade,
    Trades,
    Unknown,
)

__all__ = ["parse", "parse_exchange_timestamp"]


def parse_exchange_timestamp(value: str | None) -> int | None:
    """Convert Kraken's RFC3339 timestamp to nanoseconds since the Unix epoch.

    Returns ``None`` for a missing or unparseable value: a bad timestamp on an
    otherwise usable book frame should not kill the feed. ``datetime`` tops out
    at microseconds, so the result is exact only to 1e-6 s.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return int(dt.timestamp() * 1_000_000) * 1_000


def parse(payload: str) -> ParsedMessage:
    """Decode one frame. Undecodable input becomes :class:`ErrorMessage`."""
    try:
        obj = json.loads(payload, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError) as exc:
        return ErrorMessage(error=f"json decode failed: {exc}")

    if not isinstance(obj, dict):
        return ErrorMessage(error=f"expected a JSON object, got {type(obj).__name__}")

    channel = obj.get("channel")
    if channel == "book":
        return _parse_book(obj)
    if channel == "trade":
        return _parse_trade(obj)
    if channel == "heartbeat":
        return Heartbeat()
    if channel == "status":
        return _parse_status(obj)

    method = obj.get("method")
    if method == "pong":
        return Pong(req_id=_as_int(obj.get("req_id")))
    if method in {"subscribe", "unsubscribe"}:
        # A rejection is still an acknowledgement: Kraken answers a bad
        # subscription with success=false plus an error string on the same
        # frame. Classifying that as a generic error would leave the client
        # waiting for an ack that is never coming.
        result = obj.get("result")
        return SubscriptionAck(
            method=method,
            success=bool(obj.get("success", False)),
            req_id=_as_int(obj.get("req_id")),
            result=result if isinstance(result, dict) else None,
            error=obj.get("error"),
        )

    if "error" in obj:
        return ErrorMessage(
            error=str(obj["error"]), method=method, req_id=_as_int(obj.get("req_id"))
        )

    return Unknown(payload=obj)


def _levels(entries: Any) -> tuple[BookLevel, ...]:
    if not entries:
        return ()
    return tuple(
        BookLevel(Decimal(str(entry["price"])), Decimal(str(entry["qty"]))) for entry in entries
    )


def _parse_book(obj: dict[str, Any]) -> ParsedMessage:
    data = obj.get("data")
    if not isinstance(data, list) or not data:
        return ErrorMessage(error="book frame carried no data")

    # Kraken sends one entry per symbol. Phase one subscribes to exactly one, so
    # taking the first is correct and would need revisiting for multiple pairs
    # on one connection.
    entry = data[0]
    if not isinstance(entry, dict):
        return ErrorMessage(error="book data entry was not an object")

    try:
        symbol = str(entry["symbol"])
        bids = _levels(entry.get("bids"))
        asks = _levels(entry.get("asks"))
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        return ErrorMessage(error=f"malformed book entry: {exc}")

    checksum = _as_int(entry.get("checksum"))
    ts = parse_exchange_timestamp(entry.get("timestamp"))
    kind = obj.get("type")

    if kind == "snapshot":
        return BookSnapshot(symbol, bids, asks, checksum, ts)
    if kind == "update":
        return BookUpdate(symbol, bids, asks, checksum, ts)
    return ErrorMessage(error=f"unknown book frame type: {kind!r}")


def _parse_status(obj: dict[str, Any]) -> Status:
    data = obj.get("data")
    entry = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    return Status(
        system=entry.get("system"),
        api_version=entry.get("api_version"),
        connection_id=_as_int(entry.get("connection_id")),
    )


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# The ``trade`` frame shape, per Kraken's WebSocket v2 documentation:
#
#   {"channel": "trade", "type": "update", "data": [
#       {"symbol": "BTC/USD", "side": "buy", "price": 4136.4, "qty": 0.23374249,
#        "ord_type": "market", "trade_id": 0, "timestamp": "2022-12-25T09:30:59.123456Z"}]}
#
# Written from the published schema rather than against a live socket, because
# the machine this was developed on cannot reach Kraken. Every field is read
# defensively and a frame that does not match becomes an ErrorMessage rather
# than a silently empty batch, so a shape mismatch shows up the first time a
# capture is inspected instead of as a missing table three days later. Validate
# against a real capture before trusting any number derived from this table:
#
#   l2tca record --trades --duration 60 --out data/raw/probe.jsonl
#   l2tca inspect data/raw/probe.jsonl


def _parse_trade(obj: dict[str, Any]) -> ParsedMessage:
    data = obj.get("data")
    if not isinstance(data, list) or not data:
        return ErrorMessage(error="trade frame carried no data")

    trades: list[Trade] = []
    for entry in data:
        if not isinstance(entry, dict):
            return ErrorMessage(error="trade data entry was not an object")
        try:
            trades.append(
                Trade(
                    symbol=str(entry["symbol"]),
                    side=str(entry["side"]),
                    price=Decimal(str(entry["price"])),
                    qty=Decimal(str(entry["qty"])),
                    trade_id=_as_int(entry.get("trade_id")),
                    ord_type=(
                        str(entry["ord_type"]) if entry.get("ord_type") is not None else None
                    ),
                    exchange_ts_ns=parse_exchange_timestamp(entry.get("timestamp")),
                )
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            return ErrorMessage(error=f"malformed trade entry: {exc}")

    return Trades(symbol=trades[0].symbol, trades=tuple(trades))
