"""Execution cost analysis. CORE LOGIC -- NOT IMPLEMENTED.

# ---------------------------------------------------------------------------
# Design questions you have to settle before any of this can be written. They
# are not answered anywhere in this repository, on purpose: every one of them
# changes what the numbers mean, and inheriting someone else's choice is how a
# TCA report ends up confidently measuring the wrong thing.
#
# 1. ARRIVAL PRICE
#    Which instant, and which price at that instant?
#      - the instant: the decision, the order's arrival at the venue, the first
#        fill, something else? They differ by the latency you are trying to
#        measure, which is the point.
#      - the price: mid, touch on the trading side, micro-price, a average
#        over a short window? Each answers a different question about what
#        "the price when we started" means.
#      - the book updates continuously; a fill lands between two updates. Which
#        view counts as contemporaneous, and does the rule differ for the
#        arrival benchmark and for per-fill benchmarks?
#
# 2. CHILD ORDER SIMULATION
#      - how is the parent split: fixed slices, fixed intervals, participation
#        rate, something adaptive?
#      - when does a child fill? Only when it crosses? Does a resting child
#        ever fill, and on what evidence, given that the book channel carries
#        no trade prints?
#      - what happens to a child the book cannot fill -- does it rest, cancel,
#        or re-price?
#      - what does an unfilled remainder at the end of the horizon cost?
#
# 3. SLIPPAGE ATTRIBUTION
#      - how many layers, and which? (spread cost, market impact, timing /
#        delay, fees, opportunity cost are all candidates -- the question is
#        which ones you can actually separate with the data you have.)
#      - do the layers sum exactly to the total, or is there a residual? A
#        decomposition that does not add up is not a decomposition.
#      - what is the denominator, and is it the same for every layer?
#      - what is the sign convention, and does it hold for both sides of the
#        market?
#
# Whatever you decide, write it down in docs/ before you write the code. The
# decisions are the interesting part; the arithmetic is not.
# ---------------------------------------------------------------------------

Scope note: Kraken's ``book`` channel carries no trade prints, so any benchmark
needing traded volume (a true VWAP, participation rate) needs the ``trade``
channel, which is outside phase one. :func:`interval_vwap` is specified against
whatever volume series the caller supplies so the interface is settled now.
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
    """The benchmark price the execution is measured against.

    See question 1 at the top of this module. Both parts of it -- which instant
    and which price -- have to be decided here, and every other number in this
    module inherits the choice.

    Args:
        order: Carries ``decision_ns``; whether that is the instant you want is
            part of the question.
        views: Book views covering the execution window, ascending in
            ``recv_ns``.

    Raises:
        ValueError: Decide what makes the benchmark uncomputable, and say so
            rather than returning a number that looks usable.
    """
    
    # instant: decision time
    # price: mid price
    # last frame recv_ns <= decision time
    t = order.decision_ns
    



def interval_vwap(
    views: Sequence[BookView],
    volumes: Sequence[tuple[int, Decimal]],
    start_ns: int,
    end_ns: int,
) -> Decimal:
    """Volume-weighted benchmark price over ``[start_ns, end_ns]``.

    Args:
        views: Book views covering the window, ascending in ``recv_ns``.
        volumes: ``(ts_ns, volume)`` buckets. See the scope note above for where
            these have to come from.

    Questions:
      - Book updates arrive in bursts. What does that do to a weighting scheme
        that treats every view equally?
      - Which price from each view enters the average?
      - What happens to a bucket with no view in it, and to a view in no bucket?
    """
    raise NotImplementedError("core logic: implement by hand")


def simulate_child_orders(
    order: Order,
    views: Sequence[BookView],
    start_ns: int,
    end_ns: int,
) -> list[Fill]:
    """Simulate working ``order`` across the window, returning the fills.

    See question 2 at the top of this module. The return type is a fill list so
    the result feeds :func:`attribute_slippage` unchanged; everything about how
    those fills come to exist is yours to decide.

    Whatever the rules are, they should be visible in the output: a caller
    cannot tell a conservative fill model from an optimistic one by looking at
    an average price.
    """
    raise NotImplementedError("core logic: implement by hand")


def attribute_slippage(
    order: Order,
    fills: Sequence[Fill],
    views: Sequence[BookView],
) -> dict[str, float]:
    """Decompose the execution's cost into named components.

    See question 3 at the top of this module. Returning a ``dict`` rather than a
    typed result is deliberate: the keys are the decomposition, and naming them
    here would settle the question for you.

    Two properties worth deciding on explicitly, because they are what a reader
    will assume without checking:
      - whether the components sum to the total, and what a residual means;
      - whether a positive number is a cost or a saving, on both sides.
    """
    raise NotImplementedError("core logic: implement by hand")
