"""Value types shared by everything that reads a book.

These are written, not stubbed, for one reason: the signal and TCA tests assert
hard-coded numbers, and they need a settled input shape to assert against.
Swapping the book's *internal* representation later changes nothing here.

Nothing in this file constrains how :class:`~l2tca.book.order_book.OrderBook`
stores its state -- only what it hands out.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

__all__ = ["BookView", "Level", "Side", "TopOfBook"]


class Side(StrEnum):
    BID = "bid"
    ASK = "ask"

    @property
    def opposite(self) -> Side:
        return Side.ASK if self is Side.BID else Side.BID

    @property
    def sign(self) -> int:
        """+1 for the buy side, -1 for the sell side."""
        return 1 if self is Side.BID else -1


class Level(NamedTuple):
    """A resting price level."""

    price: Decimal
    qty: Decimal


class TopOfBook(NamedTuple):
    """Best bid and offer at an instant. Either side may be ``None``."""

    ts_ns: int
    bid: Level | None
    ask: Level | None


@dataclass(frozen=True, slots=True)
class BookView:
    """An immutable point-in-time view of the top levels of both sides.

    Sorted best-first: ``bids`` descending by price, ``asks`` ascending. A view
    is a copy, so holding one is safe while the live book keeps mutating -- and
    it is what gets written to the ``snapshot`` Parquet table.

    Attributes:
        seq: The producing book's update counter. Gives every derived row a
            replay-stable identity, which wall clock cannot.
        recv_ns: Monotonic receipt stamp of the frame that produced this view.
        exchange_ts_ns: Kraken's own timestamp, when the frame carried one.
        checksum_ok: Result of the last integrity check -- ``True``, ``False``,
            or ``None`` when the frame carried no checksum.
    """

    symbol: str
    seq: int
    recv_ns: int
    recv_wall_ns: int
    exchange_ts_ns: int | None
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    checksum_ok: bool | None = None

    @property
    def top(self) -> TopOfBook:
        return TopOfBook(
            ts_ns=self.recv_ns,
            bid=self.bids[0] if self.bids else None,
            ask=self.asks[0] if self.asks else None,
        )
