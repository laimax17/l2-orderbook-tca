"""L2 order book reconstruction. CORE LOGIC -- NOT IMPLEMENTED.

Every method below raises :class:`NotImplementedError`. The docstrings state the
input format, the complexity targets, and the questions each method has to
answer; ``tests/test_order_book.py`` pins the expected behaviour with hard-coded
values and is currently red. That red bar is the development target.

Input format
------------
Frames arrive already parsed, as :class:`~l2tca.feed.messages.BookSnapshot` and
:class:`~l2tca.feed.messages.BookUpdate` (see :mod:`l2tca.feed.parser`). Each
carries, per side, a tuple of ``(price, qty)`` pairs as :class:`decimal.Decimal`,
built from the exact digits Kraken sent. A frame may also carry a ``checksum``
and an exchange ``timestamp``; both are optional.

Complexity targets
------------------
=========================== ==========================================
``apply_update``            O(k) in the levels the frame touches, plus
                            whatever maintaining the ordering costs
``apply_snapshot``          O(depth log depth) is acceptable; it runs
                            once per (re)subscribe, not per frame
``best_bid`` / ``best_ask`` O(1)
``depth_levels(n)``         O(n)
``view(n)``                 O(n), and it runs once per applied frame --
                            so its cost is not amortised away
=========================== ==========================================

Whether those targets are met is a question for :mod:`l2tca.bench`, not for
this docstring. Run ``l2tca bench <capture>`` and read the p99.

Design questions to settle before writing this
----------------------------------------------
- What is the internal representation, and what does it cost on the read path
  versus the write path? Which of the two happens more often here?
- Prices arrive as ``Decimal``. Do they stay ``Decimal`` internally, become
  scaled integers, or something else? What does the choice cost, and what does
  it have to preserve for the integrity check in :mod:`l2tca.book.sequence`?
- The feed is depth-limited. What follows from that for levels that leave the
  window?
"""

from __future__ import annotations

from decimal import Decimal

from sortedcontainers import SortedDict

from l2tca.book.types import BookView, Level, Side
from l2tca.feed.messages import BookSnapshot, BookUpdate

__all__ = ["OrderBook"]


class OrderBook:
    """A single-symbol, depth-limited L2 book.

    Decide which invariants must hold after every applied frame, and assert them
    rather than hoping. ``tests/test_order_book.py`` states the ones this project
    expects.
    """

    def __init__(self, symbol: str, depth: int = 100) -> None:
        self.symbol = symbol
        self.depth = depth
        self.seq = 0
        self.asks = SortedDict()
        self.bids = SortedDict()

    def apply_snapshot(self, snapshot: BookSnapshot) -> None:
        """Load a full book state.

        A snapshot arrives on subscribe and after every reconnect.

        Questions:
          - What is the relationship between a snapshot and whatever the book
            already holds?
          - A snapshot could itself be malformed (crossed, or carrying a
            non-positive quantity). Is that something to absorb or to raise on?
          - How does ``seq`` relate to snapshots versus updates?
        """


        bids = SortedDict()
        asks = SortedDict()

        for price, qty in snapshot.bids:
            if qty <= 0:
                raise ValueError(f"snapshot bid at {price} has non-positive qty {qty}")
            bids[price] = Level(price, qty)

        for price, qty in snapshot.asks:
            if qty <= 0:
                raise ValueError(f"snapshot ask at {price} has non-positive qty {qty}")
            asks[price] = Level(price, qty)

        if bids and asks and bids.keys()[-1] >= asks.keys()[0]:
            raise ValueError(f"crossed snapshot: bid {bids.keys()[-1]} >= ask {asks.keys()[0]}")

        self.bids = bids
        self.asks = asks
        self.seq += 1


    def apply_update(self, update: BookUpdate) -> None:
        """Apply one incremental frame: adds, modifies and deletes, mixed.

        A level arrives as ``(price, qty)``. A quantity of zero signals that the
        level is gone; a non-zero quantity signals that the level now stands at
        that quantity.

        Questions:
          - Is the non-zero quantity a replacement or a delta? How would you
            establish which, from a capture, without reading the docs?
          - What should happen when a delete names a price the book does not
            hold? Is that a fault, or an expected consequence of the depth
            window?
          - What should happen if applying the frame leaves the book crossed
            (best bid >= best ask)? Is a crossed book a market state or a bug?
          - After the frame is applied, is the book still bounded by ``depth``?
            What makes it so?
        """

        undo: list[tuple[SortedDict, Decimal, Level | None]] = []

        try:
            for side, levels in ((self.bids, update.bids), (self.asks, update.asks)):
                for price, qty in levels:
                    if qty < 0:
                        raise ValueError(f"negative qty {qty} at {price}")
                    undo.append((side, price, side.get(price)))
                    if qty == 0:
                        # A delete for a price outside the depth window is normal.
                        side.pop(price, None)
                    else:
                        side[price] = Level(price, qty)

            # Trim the levels that fell out of the window. Only ever the worst
            # ones, so the touch -- and the crossed check below -- is unaffected.
            while len(self.bids) > self.depth:
                worst = self.bids.keys()[0]
                undo.append((self.bids, worst, self.bids[worst]))
                self.bids.pop(worst)
            while len(self.asks) > self.depth:
                worst = self.asks.keys()[-1]
                undo.append((self.asks, worst, self.asks[worst]))
                self.asks.pop(worst)

            # Checked once, against the whole frame. Kraken lifts both sides in
            # a single update, so judging each level against the half-applied
            # book would reject frames the exchange published.
            if self.best_bid and self.best_ask and self.best_bid.price >= self.best_ask.price:
                raise ValueError(
                    f"crossed after update: bid {self.best_bid.price} >= ask {self.best_ask.price}"
                )
        except ValueError:
            # Reversed, so a price touched twice in one frame is restored to
            # what it held before the frame, not to its mid-frame value.
            for side, price, previous in reversed(undo):
                if previous is None:
                    side.pop(price, None)
                else:
                    side[price] = previous
            raise

        self.seq += 1

    def clear(self) -> None:
        """Drop all state. Called on disconnect, before the replacement snapshot."""
        # seq deliberately survives. It identifies views for the life of this
        # book, and restarting it would emit duplicate book_seq values into the
        # snapshot table on either side of a reconnect.
        self.asks = SortedDict()
        self.bids = SortedDict()

    # -- reads -------------------------------------------------------------

    @property
    def best_bid(self) -> Level | None:
        """Highest resting bid, or ``None`` on an empty side. Target: O(1)."""
        if not self.bids:
            return None
        price = self.bids.keys()[-1]
        return self.bids[price]

    @property
    def best_ask(self) -> Level | None:
        """Lowest resting ask, or ``None`` on an empty side. Target: O(1)."""
        if not self.asks:
            return None
        price = self.asks.keys()[0]
        return self.asks[price]

    @property
    def mid(self) -> Decimal | None:
        """Mid price, or ``None`` when it is not defined.

        Question: when is it not defined, and what should the caller get then?
        """
        if len(self.bids) > 0 and len(self.asks) > 0:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None

    @property
    def spread(self) -> Decimal | None:
        """Quoted spread, or ``None`` when it is not defined."""
        if self.bids and self.asks:
            return (self.best_ask.price - self.best_bid.price)
        return None

    def depth_levels(self, n:int) -> tuple[tuple[Level, ...], tuple[Level, ...]]:
        """Top ``n`` levels per side, best first, as ``(bids, asks)``.

        Questions:
          - What comes back when a side holds fewer than ``n`` levels?
          - Callers keep these tuples. What does that require of what you return?
        """

        # get top n bids
        if len(self.bids) <= n:
            top_bids = []
            for x in self.bids:
                top_bids.append(Level(x,self.bids[x].qty))
            top_bids = top_bids[::-1]
            top_bids = tuple(top_bids)
        else:
            top_bids = []
            bids_keys = self.bids.keys()
            for i in range(1,n+1):
                top_bids.append(Level(bids_keys[-i],self.bids[bids_keys[-i]].qty))
            top_bids = tuple(top_bids)

        # get bottom n bids
        if len(self.asks) <= n:
            top_asks = []
            for x in self.asks:
                top_asks.append(Level(x,self.asks[x].qty))
            top_asks = tuple(top_asks)
        else:
            top_asks = []
            asks_keys = self.asks.keys()
            for i in range(n):
                top_asks.append(Level(asks_keys[i],self.asks[asks_keys[i]].qty))
            top_asks = tuple(top_asks)

        return (top_bids,top_asks)

    def view(
        self,
        n: int | None = None,
        *,
        recv_ns: int = 0,
        recv_wall_ns: int = 0,
        exchange_ts_ns: int | None = None,
        checksum_ok: bool | None = None,
    ) -> BookView:
        """Immutable copy of the top ``n`` levels (default: full ``depth``).

        The boundary between the mutable hot path and everything that reads the
        book. On the per-frame path, so its cost is part of the representation
        question above.
        """
        if n is None:
            n = self.depth
        bids, asks = self.depth_levels(n)

        return BookView(
            symbol = self.symbol,
            seq = self.seq,
            recv_ns = recv_ns,
            recv_wall_ns = recv_wall_ns,
            exchange_ts_ns = exchange_ts_ns,
            checksum_ok = checksum_ok,
            bids = bids,
            asks = asks
        )

    def quantity_to_price(self, side: Side, price: Decimal) -> Decimal:
        """Resting quantity on ``side`` at or better than ``price``.

        The primitive the TCA book walk is built on.

        Question: what does "or better" mean on each side?
        """

        if side == Side.BID:
            return sum(self.bids[p].qty for p in self.bids if p >= price)
        elif side == Side.ASK:
            return sum(self.asks[p].qty for p in self.asks if p <= price)
        else:
            raise ValueError("Invalid Side value")
