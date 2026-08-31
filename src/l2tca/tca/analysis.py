"""Execution cost analysis. CORE LOGIC -- NOT IMPLEMENTED.

The design decisions this module rests on are settled and written up in
``docs/CORE.md`` section 4, with the reasoning behind each. They are summarised
here as rules to implement against; read the reasoning there before changing
one, and replace the reasoning rather than merely contradicting it.

Notation used throughout:

======  ====================================================================
``d``   ``side.sign`` -- ``+1`` for a buy, ``-1`` for a sell
``P0``  the arrival mid
``Mi``  the mid contemporaneous with fill ``i``
``Q``   quantity filled; ``Qt`` quantity targeted
======  ====================================================================

**Contemporaneity, everywhere in this module.** The view contemporaneous with an
instant ``t`` is the last one whose ``recv_ns <= t``. Taking the next view uses a
book that had not yet arrived, which flatters every number it touches. When no
view exists at or before ``t``, raise rather than reaching forward.

**Sign, everywhere in this module.** Positive means cost, on both sides of the
market, via ``d``. Route every measure through the same helper: a per-layer sign
flip is how a report ends up flattering sells and punishing buys.

Scope note: the ``trade`` channel is recorded alongside the book, so traded
volume and executed prices are available to callers of this module from the
``trade`` Parquet table. What that does and does not change:

- :func:`interval_vwap` can now be given real volume buckets rather than
  whatever a caller invents. The function is unchanged; its input has a source.
- The simulator still fills aggressively only. Trade prints say *that* a trade
  happened at a price, not *whose* order it was. Deciding a resting child would
  have filled still needs a queue position, and L2 aggregates each level, so a
  quantity could be one order or twenty.
- Market impact is still not an attribution layer. Separating the move this
  order caused from the move the market would have made anyway needs a control,
  and observing other participants' trades is not one.

See ``docs/CORE.md`` for the reasoning behind each.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from decimal import Decimal

from l2tca.book.types import BookView, Side
from l2tca.tca.types import Fill, Order

__all__ = [
    "arrival_price",
    "attribute_slippage",
    "interval_vwap",
    "simulate_child_orders",
]


def arrival_price(order: Order, views: Sequence[BookView]) -> Decimal:
    """The mid at ``order.decision_ns``, from the view contemporaneous with it.

    The decision instant rather than arrival at the venue, so the delay between
    the two is carried as a cost rather than excused. The mid rather than the
    touch on the trading side, so that spread cost appears as its own
    attribution layer instead of being charged silently up front.

    Args:
        views: Book views covering the window, ascending in ``recv_ns``.

    Raises:
        ValueError: No view at or before ``order.decision_ns``, or that view has
            no mid. Reaching forward to the next view would be look-ahead.
    """

    # instant: decision time
    # price: mid price
    # last frame recv_ns <= decision time
    if not views:
        raise ValueError("Empty view.")
    t = order.decision_ns
    valid_views = [v for v in views if v.recv_ns <= t]
    if not valid_views:
        raise ValueError("No valid view.")
    target_view = valid_views[-1]
    p_b = target_view.bids[0].price
    p_a = target_view.asks[0].price
    return (p_a + p_b) / Decimal("2")


def interval_vwap(
    views: Sequence[BookView],
    volumes: Sequence[tuple[int, Decimal]],
    start_ns: int,
    end_ns: int,
) -> Decimal:
    """Volume-weighted mid over ``[start_ns, end_ns]``.

    For each bucket, take the mid of the view contemporaneous with its timestamp
    and weight it by that bucket's volume::

        vwap = sum(volume_i * mid_i) / sum(volume_i)

    Weighting by volume rather than by view is what makes this a market
    benchmark rather than a description of when the feed happened to be busy.

    Args:
        volumes: ``(ts_ns, volume)`` buckets. The ``book`` channel carries no
            trade prints, so these come from elsewhere -- see the scope note.

    Raises:
        ValueError: ``end_ns <= start_ns``, the volumes sum to zero, or every
            bucket was skipped for want of a view at or before it.
    """

    if end_ns <= start_ns:
        raise ValueError("Invalid time window.")

    if not views or not volumes:
        raise ValueError("Views or Volumes cannot be empty.")

    view_ts = [v.recv_ns for v in views]

    total_pv = Decimal('0')
    total_volume = Decimal('0')
    valid_buckets = 0

    for ts_ns, vol in volumes:
        if not (start_ns <= ts_ns <= end_ns):
            continue
        idx = bisect.bisect_right(view_ts, ts_ns) - 1
        if idx < 0:
            continue

        target_view = views[idx]
        p_b = target_view.bids[0].price
        p_a = target_view.asks[0].price
        p_mid = (p_b + p_a) / Decimal('2')

        total_pv += p_mid * vol
        total_volume += vol
        valid_buckets  += 1

    if valid_buckets == 0:
        raise ValueError("No valid bucket.")

    if total_volume == Decimal('0'):
        raise ValueError('Total volume is 0.')

    return total_pv/total_volume


def simulate_child_orders(
    order: Order,
    views: Sequence[BookView],
    start_ns: int,
    end_ns: int,
    *,
    slices: int = 10,
) -> list[Fill]:
    """Work ``order`` across the window on a TWAP schedule, returning the fills.

    Ten equal slices at equal intervals, unless ``slices`` says otherwise. TWAP
    because it is a real benchmark strategy with no free parameters, and because
    the alternatives are unavailable: a VWAP or participation schedule needs
    traded volume, and an adaptive one would measure a signal rather than an
    execution.

    **Every child crosses the spread and walks the opposite side.** Trade prints
    are recorded, and they are not enough: they say a trade happened at a price,
    not whose order was on the passive side of it. Deciding that a resting child
    would have filled needs a queue position, and L2 aggregates each level, so a
    quantity could be one order or twenty. This makes the result an upper bound
    on cost whose assumptions are all visible in the book, rather than a lower
    one resting on a queue model nothing here can validate.

    One :class:`Fill` per book level consumed, at that level's own price: a
    single averaged fill per slice would hide that the order walked five levels.

    When the visible book cannot fill a slice, fill what is there and carry the
    remainder to the next one; never extrapolate past the last level. Quantity
    still unfilled when the window closes stays unfilled -- its cost is
    opportunity cost, and belongs in :func:`attribute_slippage`.
    """
    if not views or start_ns >= end_ns or order.target_qty <= Decimal("0"):
        return []

    time_step = (end_ns - start_ns) // slices
    schedule_times = [start_ns + i * time_step for i in range(slices)]

    base_slice_qty = order.target_qty / Decimal(slices)
    remaining_total_qty = order.target_qty
    carry_over_qty = Decimal("0")

    fills: list[Fill] = []
    view_timestamps = [v.recv_ns for v in views]
    is_buy = order.side == Side('bid')

    for t_ns in schedule_times:
        if remaining_total_qty <= Decimal("0"):
            break

        target_qty = min(base_slice_qty + carry_over_qty, remaining_total_qty)

        idx = bisect.bisect_right(view_timestamps, t_ns) - 1
        if idx < 0:
            carry_over_qty = target_qty
            continue

        curr_view = views[idx]
        depth = curr_view.asks if is_buy else curr_view.bids

        filled_in_this_slice = Decimal("0")

        for level in depth:
            needed = target_qty - filled_in_this_slice
            if needed <= Decimal("0"):
                break

            take_qty = min(needed, level.qty)
            if take_qty > Decimal("0"):
                fills.append(
                    Fill(
                        price=level.price,
                        qty=take_qty,
                        ts_ns=t_ns,
                    )
                )
                filled_in_this_slice += take_qty

        remaining_total_qty -= filled_in_this_slice
        carry_over_qty = target_qty - filled_in_this_slice

    return fills


def _get_mid(view: BookView) -> Decimal:
    """Get view mid price"""
    return (view.bids[0].price + view.asks[0].price) / Decimal("2")


def _find_view_mid_at(ts_ns: int, views: Sequence[BookView]) -> Decimal:
    """
    Find the last view where recv_ns <= ts_ns.
    """
    view_timestamps = [v.recv_ns for v in views]
    idx = bisect.bisect_right(view_timestamps, ts_ns) - 1
    if idx < 0:
        raise ValueError("cannot find target view.")
    target_view = views[idx]
    return _get_mid(target_view)


def attribute_slippage(
    order: Order,
    fills: Sequence[Fill],
    views: Sequence[BookView],
) -> dict[str, float]:
    """Decompose the execution's cost into four layers that sum to the total."""
    if not views:
        raise ValueError("views sequence cannot be empty")

    d = Decimal("1") if order.side == Side('bid') else Decimal("-1")

    Q_t = order.target_qty

    P0 = _find_view_mid_at(order.decision_ns, views)

    P_end = _get_mid(views[-1])

    target_notional = Q_t * P0

    spread_ccy = Decimal("0")
    timing_ccy = Decimal("0")
    fees_ccy = Decimal("0")
    Q_filled = Decimal("0")

    for fill in fills:
        q_i = fill.qty
        P_i = fill.price
        fee_i = getattr(fill, "fee", Decimal("0"))

        M_i = _find_view_mid_at(fill.ts_ns, views)

        spread_ccy += q_i * (P_i - M_i) * d
        timing_ccy += q_i * (M_i - P0) * d
        fees_ccy += fee_i

        Q_filled += q_i

    unfilled_qty = Q_t - Q_filled
    opportunity_ccy = unfilled_qty * (P_end - P0) * d

    bps_scale = Decimal("10000") / target_notional

    spread_bps = float(spread_ccy * bps_scale)
    timing_bps = float(timing_ccy * bps_scale)
    fees_bps = float(fees_ccy * bps_scale)
    opportunity_bps = float(opportunity_ccy * bps_scale)

    total_bps = spread_bps + timing_bps + fees_bps + opportunity_bps

    return {
        "spread_bps": spread_bps,
        "timing_bps": timing_bps,
        "fees_bps": fees_bps,
        "opportunity_bps": opportunity_bps,
        "total_bps": total_bps,
    }
