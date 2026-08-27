"""Wire format for Kraken's public WebSocket v2 ``book`` channel.

Two layers live here:

``RawMessage``
    The exact text Kraken sent, plus the two clocks we stamp it with. This is
    what gets recorded to disk, and it is the only thing replay needs to be
    byte-for-byte faithful.

``ParsedMessage``
    A normalised, typed view of that text. Parsing is deliberately separate from
    receiving so that it can be benchmarked, fuzzed and replayed in isolation.

Numeric policy
--------------
Prices and quantities are parsed as :class:`decimal.Decimal` built directly from
the JSON literal (``json.loads(..., parse_float=Decimal)``). Two reasons:

1. Kraken's book checksum is computed over the *exact* decimal digits the
   exchange sent. Round-tripping through binary float can change those digits,
   which would make a correct checksum implementation report corruption.
2. Price levels are dictionary keys in the book. Float keys make
   ``0.1 + 0.2`` style drift a correctness bug rather than a display bug.

The cost is that ``Decimal`` arithmetic is slower than ``float``. The book
implementation is free to convert to scaled integers (price ticks) internally --
see ``docs/SPEC.md`` -- as long as it can reproduce the original digits when
computing a checksum. Conversion to ``float64`` happens at the Parquet boundary
and nowhere else.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

__all__ = [
    "BookLevel",
    "BookSnapshot",
    "BookUpdate",
    "ErrorMessage",
    "Heartbeat",
    "ParsedMessage",
    "Pong",
    "RawMessage",
    "Status",
    "SubscriptionAck",
    "Unknown",
    "parse",
    "parse_exchange_timestamp",
]


class BookLevel(NamedTuple):
    """One price level. ``qty == 0`` means "delete this level"."""

    price: Decimal
    qty: Decimal


@dataclass(frozen=True, slots=True)
class RawMessage:
    """One inbound frame, exactly as received, with both clocks attached.

    Attributes:
        seq: Monotonic counter assigned by the source, starting at 0. Survives
            reconnects so gaps in a recording are visible.
        recv_ns: ``time.perf_counter_ns()`` at receipt. Monotonic and immune to
            NTP steps, so it is the only clock used for latency arithmetic.
            It has no meaning across processes.
        recv_wall_ns: ``time.time_ns()`` at receipt. Comparable across machines
            and against exchange timestamps, but can jump.
        payload: The raw frame text. Never re-serialised, so a recording is a
            faithful copy of the session.
    """

    seq: int
    recv_ns: int
    recv_wall_ns: int
    payload: str

    @classmethod
    def stamp(cls, payload: str, seq: int) -> RawMessage:
        """Build a message stamped with both clocks, reading them back to back."""
        return cls(
            seq=seq,
            recv_ns=time.perf_counter_ns(),
            recv_wall_ns=time.time_ns(),
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Full book state for ``symbol``, replacing anything held before it."""

    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    checksum: int | None
    exchange_ts_ns: int | None


@dataclass(frozen=True, slots=True)
class BookUpdate:
    """Incremental change. Levels with ``qty == 0`` are removals."""

    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    checksum: int | None
    exchange_ts_ns: int | None


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Kraken sends this at least once a second on a subscribed connection."""


@dataclass(frozen=True, slots=True)
class Status:
    """Connection status frame (``system``: online/maintenance/...)."""

    system: str | None
    api_version: str | None
    connection_id: int | None


@dataclass(frozen=True, slots=True)
class SubscriptionAck:
    """Reply to a ``subscribe``/``unsubscribe`` request."""

    method: str
    success: bool
    req_id: int | None
    result: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True, slots=True)
class Pong:
    """Reply to an application-level ``ping``."""

    req_id: int | None


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    """A frame Kraken flagged as an error, or one we could not decode."""

    error: str
    method: str | None = None
    req_id: int | None = None


@dataclass(frozen=True, slots=True)
class Unknown:
    """A well-formed frame on a channel or method we do not model."""

    payload: dict[str, Any]


ParsedMessage = (
    BookSnapshot | BookUpdate | Heartbeat | Status | SubscriptionAck | Pong | ErrorMessage | Unknown
)


def parse_exchange_timestamp(value: str | None) -> int | None:
    """Convert Kraken's RFC3339 timestamp to nanoseconds since the Unix epoch.

    Returns ``None`` for a missing or unparseable value rather than raising: a
    malformed timestamp on an otherwise usable book update should not kill the
    feed. Note that Python's ``datetime`` tops out at microsecond resolution, so
    the returned value is exact only to 1e-6 s.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return int(dt.timestamp() * 1_000_000) * 1_000


def _levels(entries: Any) -> tuple[BookLevel, ...]:
    if not entries:
        return ()
    out = []
    for entry in entries:
        out.append(BookLevel(Decimal(str(entry["price"])), Decimal(str(entry["qty"]))))
    return tuple(out)


def parse(payload: str) -> ParsedMessage:
    """Decode one frame. Never raises: undecodable input becomes ``ErrorMessage``.

    The feed must survive anything the exchange puts on the wire, so every
    failure path here is a value, not an exception.
    """
    try:
        obj = json.loads(payload, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError) as exc:
        return ErrorMessage(error=f"json decode failed: {exc}")

    if not isinstance(obj, dict):
        return ErrorMessage(error=f"expected a JSON object, got {type(obj).__name__}")

    channel = obj.get("channel")
    if channel == "book":
        return _parse_book(obj)
    if channel == "heartbeat":
        return Heartbeat()
    if channel == "status":
        return _parse_status(obj)

    method = obj.get("method")
    if method == "pong":
        return Pong(req_id=_as_int(obj.get("req_id")))
    if method in {"subscribe", "unsubscribe"}:
        # A rejection is still an acknowledgement: Kraken answers a bad
        # subscription with ``success: false`` plus an ``error`` string on the
        # same frame. Classifying it as a generic error instead would leave the
        # client waiting for an ack that is never coming.
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
            error=str(obj["error"]),
            method=method,
            req_id=_as_int(obj.get("req_id")),
        )

    return Unknown(payload=obj)


def _parse_book(obj: dict[str, Any]) -> ParsedMessage:
    data = obj.get("data")
    if not isinstance(data, list) or not data:
        return ErrorMessage(error="book frame carried no data")

    # Kraken sends one entry per symbol; phase one subscribes to exactly one, so
    # taking the first entry is correct here and would need revisiting the day
    # this grows to multiple pairs on one connection.
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
