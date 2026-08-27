"""L2 order book reconstruction -- CORE LOGIC, INTENTIONALLY UNIMPLEMENTED.

Every method below raises :class:`NotImplementedError` on purpose. This is the
part of the project that is meant to be written by hand; the docstrings are the
specification, and ``tests/spec/test_book_spec.py`` is the executable version of
that specification (currently ``xfail``). Implement a method, drop its ``xfail``
marker, and the suite tells you whether you got it right.

Nothing else in the repository is stubbed. The feed, recorder, replay, Parquet
layer, CLI and benchmark harness are complete and tested, so the first line
written here can immediately be run against a recorded session and benchmarked.

Design notes worth deciding before writing code
-----------------------------------------------
*Internal representation.* The obvious choice is two ``dict[Decimal, Decimal]``
plus a sort on read; that is O(1) to apply and O(n log n) to read, which is
backwards for a feed where reads (one per update, to emit a view) are as
frequent as writes. A ``sortedcontainers.SortedDict`` inverts that trade-off,
and an array of price *ticks* (integer ``price / tick_size``) with the top of
book cached inverts it further at the cost of a fixed price band. The benchmark
harness in :mod:`l2tca.bench` exists so this is answered with numbers rather
than opinion -- measure at least the p99, since it is tail latency that
determines whether a strategy sees a stale book.

*Depth truncation.* Kraken pushes a depth-limited book (100 levels here). A
level falling out of the bottom of the window is *not* announced as a deletion,
so a naive implementation accumulates stale levels below the window forever.
The book must therefore trim to ``depth`` after every update, and the checksum
(top 10 only) will not catch the bug.

*Decimal versus float.* See the numeric policy in :mod:`l2tca.feed.messages`.
Whatever the internal representation, the checksum has to be computable from
the exact digits Kraken sent.
"""

from __future__ import annotations

from decimal import Decimal

from l2tca.book.base import BookView, Level
from l2tca.feed.messages import BookSnapshot, BookUpdate

__all__ = ["L2Book", "kraken_book_checksum"]


class L2Book:
    """A single-symbol, depth-limited L2 book.

    Invariants an implementation must maintain after every applied frame:

    1. No level has ``qty <= 0``; a zero-quantity update is a deletion.
    2. ``bids`` is strictly descending in price, ``asks`` strictly ascending,
       with no duplicate prices on either side.
    3. ``len(bids) <= depth`` and ``len(asks) <= depth``.
    4. The book is not crossed: ``best_bid < best_ask`` whenever both exist.
       A crossed book is always a bug in reconstruction, never a market state
       the exchange published -- treat it as a hard error, not a warning.
    5. ``seq`` increments by exactly one per applied frame.
    """

    def __init__(self, symbol: str, depth: int = 100) -> None:
        self.symbol = symbol
        self.depth = depth
        self.seq = 0
        self.checksum_failures = 0

    # -- mutation ----------------------------------------------------------

    def apply_snapshot(self, snapshot: BookSnapshot) -> None:
        """Replace all state with ``snapshot``.

        A snapshot is authoritative and unconditional: it arrives on subscribe
        and after every reconnect, and anything held before it is stale by
        definition. Discard first, then load -- do not merge.

        Must reset ``seq`` bookkeeping consistently with :meth:`apply_update`
        (either both count frames from zero, or the snapshot counts as frame
        one; pick one and make the spec test agree).

        Raises:
            ValueError: If the snapshot is itself crossed or carries a level
                with non-positive quantity. That is an exchange-side or
                parser-side fault and must not be silently absorbed.
        """
        raise NotImplementedError("core logic: implement by hand")

    def apply_update(self, update: BookUpdate) -> None:
        """Apply one incremental frame.

        For each level in ``update.bids`` and ``update.asks``:

        * ``qty == 0`` removes the price level if present. A delete for a price
          that is not in the book is *normal*, not an error: it refers to a level
          that fell below the depth window.
        * ``qty > 0`` inserts the level, or replaces the quantity at an existing
          price. Kraken sends absolute resting quantity, never a delta -- adding
          to the existing quantity is the classic wrong implementation and shows
          up as a slowly diverging checksum rather than an immediate failure.

        After applying every level, trim each side back to ``self.depth``.

        If the frame carries a checksum, verify it (see
        :func:`kraken_book_checksum`), increment ``checksum_failures`` on
        mismatch, and let the caller decide whether to resubscribe. A mismatch
        means the local book has silently diverged from the exchange's, so
        continuing to trade off it is worse than a gap.
        """
        raise NotImplementedError("core logic: implement by hand")

    def clear(self) -> None:
        """Drop all state. Called on disconnect, before the replacement snapshot."""
        raise NotImplementedError("core logic: implement by hand")

    # -- reads -------------------------------------------------------------

    @property
    def best_bid(self) -> Level | None:
        """Highest resting bid, or ``None`` on an empty side. Must be O(1)."""
        raise NotImplementedError("core logic: implement by hand")

    @property
    def best_ask(self) -> Level | None:
        """Lowest resting ask, or ``None`` on an empty side. Must be O(1)."""
        raise NotImplementedError("core logic: implement by hand")

    @property
    def mid(self) -> Decimal | None:
        """``(best_bid + best_ask) / 2``, or ``None`` if either side is empty."""
        raise NotImplementedError("core logic: implement by hand")

    @property
    def spread(self) -> Decimal | None:
        """``best_ask - best_bid``, or ``None`` if either side is empty."""
        raise NotImplementedError("core logic: implement by hand")

    def depth_levels(self, n: int) -> tuple[tuple[Level, ...], tuple[Level, ...]]:
        """Top ``n`` levels per side, best first, as ``(bids, asks)``.

        Returns fewer than ``n`` when the side is thinner. Must not expose
        internal mutable state -- callers keep these around.
        """
        raise NotImplementedError("core logic: implement by hand")

    def view(self, n: int | None = None, *, recv_ns: int = 0, recv_wall_ns: int = 0,
             exchange_ts_ns: int | None = None, checksum_ok: bool | None = None) -> BookView:
        """Immutable copy of the top ``n`` levels (default: full ``depth``).

        This is the boundary between the mutable hot path and everything that
        reads the book. It is on the per-update path, so its cost is the reason
        the internal representation matters; measure it with
        :mod:`l2tca.bench`.
        """
        raise NotImplementedError("core logic: implement by hand")

    def notional_to_price(self, side: str, price: Decimal) -> Decimal:
        """Resting quantity available on ``side`` at or better than ``price``.

        The primitive the TCA layer's book walk is built on. ``side='ask'``
        answers "how much can a buyer lift before the price exceeds ``price``".
        """
        raise NotImplementedError("core logic: implement by hand")


def kraken_book_checksum(
    bids: tuple[Level, ...],
    asks: tuple[Level, ...],
    price_precision: int,
    qty_precision: int,
) -> int:
    """CRC32 checksum over the top of book, matching Kraken's ``checksum`` field.

    The exchange's own integrity check, and the single most valuable test in the
    project: it turns a silently wrong book into a loud failure within seconds.

    Algorithm (Kraken WebSocket v2):

    1. Take the top 10 asks (ascending price), then the top 10 bids
       (descending price). Fewer than 10 on a side means use what is there.
    2. For each level, render ``price`` with exactly ``price_precision``
       decimals and ``qty`` with exactly ``qty_precision`` decimals, using plain
       fixed-point notation -- no exponent, no thousands separator.
    3. Remove the decimal point, then strip leading zeros from what remains.
    4. Concatenate: for each ask, its price string then its quantity string;
       then the same for each bid.
    5. ``zlib.crc32`` of that ASCII string, as an unsigned 32-bit integer.

    The precisions are per-pair and come from Kraken's ``instrument`` channel or
    the ``AssetPairs`` REST endpoint (``pair_decimals`` / ``lot_decimals``);
    they are not derivable from the book frames themselves. Getting them wrong
    makes every checksum fail, which is a useful early signal that they are
    plumbed through rather than guessed.

    Two traps worth knowing before writing this:

    * ``Decimal.__str__`` switches to exponent notation for small values, so
      ``0.0000001`` must be rendered via quantisation or formatting, not ``str``.
    * Step 3 strips leading zeros from the *concatenated digits*, so a price
      below 1 loses its leading zero -- ``0.5`` at 5 decimals is ``50000``.

    Returns:
        The unsigned CRC32 value, directly comparable to the frame's
        ``checksum`` field.
    """
    raise NotImplementedError("core logic: implement by hand")
