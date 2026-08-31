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

Scope note: Kraken's ``book`` channel carries no trade prints. That is why the
simulator fills aggressively only, and why market impact is not an attribution
layer -- see ``docs/CORE.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from l2tca.book.types import BookView
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
    raise NotImplementedError("core logic: implement by hand")


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
    raise NotImplementedError("core logic: implement by hand")


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

    **Every child crosses the spread and walks the opposite side.** Without trade
    prints there is no evidence of when a resting order would have filled, and
    inferring it needs a queue position that L2 data does not carry -- it
    aggregates each level, so a quantity could be one order or twenty. This
    makes the result an upper bound on cost whose assumptions are all visible in
    the book, rather than a lower one resting on a queue model nothing can
    validate.

    One :class:`Fill` per book level consumed, at that level's own price: a
    single averaged fill per slice would hide that the order walked five levels.

    When the visible book cannot fill a slice, fill what is there and carry the
    remainder to the next one; never extrapolate past the last level. Quantity
    still unfilled when the window closes stays unfilled -- its cost is
    opportunity cost, and belongs in :func:`attribute_slippage`.
    """
    raise NotImplementedError("core logic: implement by hand")


def attribute_slippage(
    order: Order,
    fills: Sequence[Fill],
    views: Sequence[BookView],
) -> dict[str, float]:
    """Decompose the execution's cost into four layers that sum to the total.

    In currency, before scaling::

        spread_ccy       =  sum over fills of  qi * (Pi - Mi) * d
        timing_ccy       =  sum over fills of  qi * (Mi - P0) * d
        fees_ccy         =  sum over fills of  fee_i
        opportunity_ccy  =  (Qt - Q) * (Pend - P0) * d

    The first two sum by construction rather than by approximation, since
    ``(Pi - Mi) + (Mi - P0)`` collapses to ``(Pi - P0)``; fees and opportunity
    cost complete the shortfall. A decomposition with a residual is not one.

    Every layer is divided by the same denominator, the *target* notional
    ``Qt * P0``, and scaled to basis points. Dividing by filled notional instead
    makes a badly underfilled order look cheap -- precisely the case shortfall
    exists to penalise -- and layers with different denominators cannot add up.

    Returns:
        ``spread_bps``, ``timing_bps``, ``fees_bps``, ``opportunity_bps`` and
        ``total_bps``. All five keys are present whether or not anything filled;
        with no fills the first three are zero and opportunity carries the whole
        order.

    Market impact is deliberately absent. Separating the move this order caused
    from the move the market would have made anyway needs a control or a trade
    feed, and neither exists here, so the move is reported whole as ``timing``.
    A number labelled "impact" produced without either would look authoritative
    and mean nothing.
    """
    raise NotImplementedError("core logic: implement by hand")
