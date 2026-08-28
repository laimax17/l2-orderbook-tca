"""Message types for Kraken's public WebSocket v2 ``book`` channel.

Types only; decoding lives in :mod:`l2tca.feed.parser`. Receiving and parsing
are separate so each can be benchmarked, fuzzed and replayed on its own.

Numeric policy
--------------
Prices and quantities are :class:`decimal.Decimal`, built from the JSON literal
rather than from a float. Kraken's book checksum is computed over the exact
decimal digits the exchange sent, and price levels are used as keys, so binary
float would make digit-level drift a correctness problem. ``float`` appears only
at the Parquet boundary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
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
]


class BookLevel(NamedTuple):
    """One price level as it arrived. ``qty`` may be zero."""

    price: Decimal
    qty: Decimal


@dataclass(frozen=True, slots=True)
class RawMessage:
    """One inbound frame, exactly as received, with both clocks attached.

    Attributes:
        seq: Monotonic counter assigned by the source, starting at 0. Continues
            across reconnects, so a gap in a recording is visible.
        recv_ns: ``time.perf_counter_ns()`` at receipt. Monotonic and immune to
            NTP steps, so it is the only clock used for latency arithmetic. It
            has no epoch and means nothing across processes.
        recv_wall_ns: ``time.time_ns()`` at receipt. Comparable across machines
            and against exchange timestamps, but can jump.
        payload: The raw frame text, never re-serialised.
    """

    seq: int
    recv_ns: int
    recv_wall_ns: int
    payload: str

    @classmethod
    def stamp(cls, payload: str, seq: int) -> RawMessage:
        """Build a message stamped with both clocks, read back to back."""
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
    """An incremental book frame."""

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
    """Connection status frame (``system``: online / maintenance / ...)."""

    system: str | None
    api_version: str | None
    connection_id: int | None


@dataclass(frozen=True, slots=True)
class SubscriptionAck:
    """Reply to a ``subscribe`` / ``unsubscribe`` request, success or not."""

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
    """A frame Kraken flagged as an error, or one that could not be decoded."""

    error: str
    method: str | None = None
    req_id: int | None = None


@dataclass(frozen=True, slots=True)
class Unknown:
    """A well-formed frame on a channel or method this build does not model."""

    payload: dict[str, Any]


ParsedMessage = (
    BookSnapshot | BookUpdate | Heartbeat | Status | SubscriptionAck | Pong | ErrorMessage | Unknown
)
