"""Concrete value types shared by everything that reads a book.

These are deliberately *not* stubs. Signals, TCA and the Parquet writers are all
written against them, so the shape of the data is settled before the
reconstruction algorithm is written, and swapping in a faster internal
representation later changes nothing above this line.
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
    """A resting price level. ``qty`` is always strictly positive in a book view."""

    price: Decimal
    qty: Decimal


class TopOfBook(NamedTuple):
    """Best bid and offer at an instant. Either side may be ``None`` on an empty book."""

    ts_ns: int
    bid: Level | None
    ask: Level | None

    @property
    def mid(self) -> Decimal | None:
        """Arithmetic mid. ``None`` when the book is one-sided."""
        if self.bid is None or self.ask is None:
            return None
        return (self.bid.price + self.ask.price) / 2

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask.price - self.bid.price


@dataclass(frozen=True, slots=True)
class BookView:
    """An immutable point-in-time view of the top ``len(bids)`` levels.

    Sorted best-first: ``bids`` descending by price, ``asks`` ascending. A view
    is a copy, so holding one is safe while the live book keeps mutating -- and
    it is what gets written to the ``snapshot`` Parquet table.

    Attributes:
        seq: Count of updates applied to produce this view. Gives every derived
            row a deterministic, replay-stable identity, which wall clock cannot.
        recv_ns: Monotonic receipt stamp of the frame that produced this view.
        exchange_ts_ns: Kraken's own timestamp, when the frame carried one.
        checksum_ok: Result of the last checksum verification -- ``True``,
            ``False``, or ``None`` when the frame carried no checksum.
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

    @property
    def is_crossed(self) -> bool:
        """A crossed book (best bid >= best ask) always means a reconstruction bug."""
        if not self.bids or not self.asks:
            return False
        return self.bids[0].price >= self.asks[0].price
