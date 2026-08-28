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

    # -- mutation ----------------------------------------------------------

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
        raise NotImplementedError("core logic: implement by hand")

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
        raise NotImplementedError("core logic: implement by hand")

    def clear(self) -> None:
        """Drop all state. Called on disconnect, before the replacement snapshot."""
        raise NotImplementedError("core logic: implement by hand")

    # -- reads -------------------------------------------------------------

    @property
    def best_bid(self) -> Level | None:
        """Highest resting bid, or ``None`` on an empty side. Target: O(1)."""
        raise NotImplementedError("core logic: implement by hand")

    @property
    def best_ask(self) -> Level | None:
        """Lowest resting ask, or ``None`` on an empty side. Target: O(1)."""
        raise NotImplementedError("core logic: implement by hand")

    @property
    def mid(self) -> Decimal | None:
        """Mid price, or ``None`` when it is not defined.

        Question: when is it not defined, and what should the caller get then?
        """
        raise NotImplementedError("core logic: implement by hand")

    @property
    def spread(self) -> Decimal | None:
        """Quoted spread, or ``None`` when it is not defined."""
        raise NotImplementedError("core logic: implement by hand")

    def depth_levels(self, n: int) -> tuple[tuple[Level, ...], tuple[Level, ...]]:
        """Top ``n`` levels per side, best first, as ``(bids, asks)``.

        Questions:
          - What comes back when a side holds fewer than ``n`` levels?
          - Callers keep these tuples. What does that require of what you return?
        """
        raise NotImplementedError("core logic: implement by hand")

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
        raise NotImplementedError("core logic: implement by hand")

    def quantity_to_price(self, side: Side, price: Decimal) -> Decimal:
        """Resting quantity on ``side`` at or better than ``price``.

        The primitive the TCA book walk is built on.

        Question: what does "or better" mean on each side?
        """
        raise NotImplementedError("core logic: implement by hand")
