"""Execution cost measures -- CORE LOGIC, INTENTIONALLY UNIMPLEMENTED.

Every function raises :class:`NotImplementedError`; the docstring is the
specification and ``tests/spec/test_tca_spec.py`` is the executable version.

Sign convention, applied without exception
------------------------------------------
Every ``*_bps`` result is **signed so that positive means cost**, on both sides:

* Buy:  ``(execution_price - benchmark) / benchmark * 1e4``
* Sell: ``(benchmark - execution_price) / benchmark * 1e4``

Equivalently, multiply the buy-side formula by ``side.sign``. Every function
below must go through the same helper for this, because a per-function sign
flip is the classic way a TCA report ends up flattering sells and punishing
buys.

Benchmark selection
-------------------
Each measure differs only in what it compares the fills against, and each
answers a different question:

* **arrival mid** -- did we beat the price at the decision instant? Measures
  the whole cost of the execution, including the market moving while we worked.
* **quote mid at fill time** -- did each individual fill cross a wide or a
  narrow spread? Measures the trading desk, not the market.
* **mid some horizon after the fill** -- how much of what we paid was permanent
  information versus temporary pressure that reverted?
* **interval VWAP/TWAP** -- did we beat a passive schedule over the same window?
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from l2tca.book.base import BookView, Side
from l2tca.tca.base import Fill, Order, TcaResult

__all__ = [
    "arrival_slippage_bps",
    "effective_spread_bps",
    "implementation_shortfall_bps",
    "participation_weighted_price",
    "realized_spread_bps",
    "run_tca",
    "twap_benchmark",
    "walk_the_book_cost",
]


def arrival_slippage_bps(order: Order, fills: Sequence[Fill], arrival_mid: Decimal) -> float:
    """Average fill price versus the mid at ``order.arrival_ns``, in bps.

    The headline number on most TCA reports. Quantity-weight the fills into one
    average price, compare to ``arrival_mid``, and apply the sign convention
    above.

    Excludes fees -- :func:`implementation_shortfall_bps` adds those. Keeping
    them separate matters because fees are a known, negotiable constant while
    slippage is the part execution quality actually controls.

    Returns ``nan`` on zero filled quantity or a non-positive ``arrival_mid``.
    """
    raise NotImplementedError("core logic: implement by hand")


def implementation_shortfall_bps(
    order: Order,
    fills: Sequence[Fill],
    arrival_mid: Decimal,
    *,
    final_mid: Decimal | None = None,
) -> float:
    """Full Perold implementation shortfall in bps of arrival notional.

    Three components, all measured against the arrival mid and all expressed
    per unit of the *target* quantity, not the filled quantity:

    1. **Execution cost** -- filled quantity times (average price - arrival
       mid), signed. What the fills actually cost against the decision price.
    2. **Fees** -- summed ``Fill.fee``, in quote currency.
    3. **Opportunity cost** -- unfilled quantity times (final mid - arrival
       mid), signed. The cost of *not* trading. Requires ``final_mid``; when it
       is ``None``, report only the first two and record the omission in
       :attr:`TcaResult.components` rather than silently returning a smaller
       number that looks like better execution.

    The denominator is ``order.target_qty * arrival_mid``. Dividing by filled
    notional instead is the common error, and it makes a badly underfilled
    order look cheap -- precisely the case shortfall exists to penalise.

    Returns ``nan`` when ``target_qty`` or ``arrival_mid`` is non-positive.
    """
    raise NotImplementedError("core logic: implement by hand")


def effective_spread_bps(fill: Fill, view_at_fill: BookView, side: Side) -> float:
    """Twice the signed distance from the fill price to the contemporaneous mid.

    ``2 * side.sign * (fill.price - mid) / mid * 1e4``.

    The factor of two makes it comparable to the full quoted spread rather than
    a half-spread: a marketable order that pays exactly the touch has an
    effective spread equal to the quoted spread. Effective narrower than quoted
    means price improvement (a hidden or midpoint fill); wider means the order
    walked the book.

    ``view_at_fill`` must be the book state *immediately before* the fill --
    using the post-fill view compares the fill against a book it already
    consumed, which understates the cost of every aggressive order.

    Returns ``nan`` if the mid is undefined.
    """
    raise NotImplementedError("core logic: implement by hand")


def realized_spread_bps(
    fill: Fill,
    view_after: BookView,
    side: Side,
    *,
    horizon_ns: int = 60_000_000_000,
) -> float:
    """Effective spread measured against the mid ``horizon_ns`` after the fill.

    ``2 * side.sign * (fill.price - mid_after) / mid_after * 1e4``, where
    ``view_after`` is the book at ``fill.ts_ns + horizon_ns``.

    Decomposes the effective spread: ``effective = realized + 2 * impact``.
    Realized spread is the part the liquidity provider keeps once the price has
    finished moving; price impact is the part that was information. A large
    effective spread that is mostly impact means the order was informed and
    moved the market. Mostly realized means it simply paid a wide spread.

    The horizon is a real modelling choice -- five minutes is the equity
    convention, but crypto books mean-revert faster; sweep it against a
    recording rather than inheriting it.

    Returns ``nan`` if the post-horizon mid is undefined.
    """
    raise NotImplementedError("core logic: implement by hand")


def walk_the_book_cost(
    view: BookView,
    side: Side,
    qty: Decimal,
) -> tuple[Decimal, Decimal, bool]:
    """Cost of immediately executing ``qty`` by consuming resting liquidity.

    Consume levels best-first on the *opposite* side (a buy lifts asks), taking
    the full quantity at each level until ``qty`` is exhausted.

    Returns:
        ``(average_price, worst_price, fully_filled)``. When the visible book is
        too thin, fill what is there, set ``fully_filled=False``, and return the
        average over the *filled* portion. Do not extrapolate the last level --
        an invented price for the unfillable remainder is a fabricated number
        that will be read as a real cost estimate.

    This is the pre-trade counterpart to the post-trade measures above, and the
    only one that gives a cost estimate for an order that was never sent. Its
    accuracy is bounded by the depth of the feed: at ``depth=100`` on a liquid
    pair the visible book is a few hundred thousand dollars deep, so estimates
    for larger orders are extrapolation and should be reported as such.

    Raises:
        ValueError: If ``qty <= 0``.
    """
    raise NotImplementedError("core logic: implement by hand")


def twap_benchmark(views: Sequence[BookView], start_ns: int, end_ns: int) -> Decimal:
    """Time-weighted average mid over ``[start_ns, end_ns]``.

    Weight each view's mid by how long it stood -- the interval to the next
    view, clipped to the window -- not by an unweighted average over views. Book
    updates arrive in bursts, so an unweighted mean silently overweights busy
    microseconds and turns a "time-weighted" benchmark into an event-weighted
    one.

    Views with an undefined mid are skipped, and their duration is redistributed
    to the previous valid view.

    Raises:
        ValueError: If ``end_ns <= start_ns`` or no view has a defined mid.
    """
    raise NotImplementedError("core logic: implement by hand")


def participation_weighted_price(
    fills: Sequence[Fill],
    market_volume: Sequence[tuple[int, Decimal]],
) -> Decimal:
    """Benchmark price weighted by market volume over the execution window.

    ``market_volume`` is ``(ts_ns, volume)`` buckets. Weight each bucket's fill
    activity by market volume in the same bucket, so the benchmark reflects how
    much of the day's liquidity the order actually competed for.

    Note the scope limit: Kraken's ``book`` channel carries no trade prints, so
    ``market_volume`` has to come from the ``trade`` channel -- which is outside
    phase one. This function is specified now so the interface is settled, and
    is the natural first extension once a trade feed is recorded alongside.

    Raises:
        ValueError: If ``market_volume`` is empty or sums to zero.
    """
    raise NotImplementedError("core logic: implement by hand")


def run_tca(
    order: Order,
    fills: Sequence[Fill],
    views: Sequence[BookView],
) -> TcaResult:
    """Assemble the full :class:`TcaResult` for one parent order.

    Locates the arrival view (the last view at or before ``order.arrival_ns``)
    and the per-fill views, calls each measure above, and fills in
    :attr:`TcaResult.components` with the intermediate values -- the arrival
    mid, the fee total, the filled fraction -- so a surprising headline number
    can be taken apart without re-running anything.

    Every measure that cannot be computed is left as ``nan`` and the reason is
    recorded in ``components``. Silently substituting zero for an unavailable
    benchmark is how a TCA report ends up confidently wrong.
    """
    raise NotImplementedError("core logic: implement by hand")
