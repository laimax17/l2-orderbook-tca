"""Specification for :mod:`l2tca.tca.analysis`, as executable tests.

FAILS until the analysis is written.

**These assert properties, not values, and that is deliberate.** The three
design questions at the top of ``l2tca/tca/analysis.py`` -- which instant and
which price make the arrival benchmark, how a parent order is split and filled,
how slippage decomposes -- are yours to answer, and a test asserting
``arrival_price(...) == 100.5`` would answer the first one for you.

So what is pinned here is everything that must hold *whatever* you decide: a
benchmark price lies inside the book that produced it, a simulation cannot fill
more than the parent quantity, a decomposition is finite and internally
consistent. Once you have made the decisions, add the value tests -- they will
be short, and they will be yours.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from tests.factories import book_view, fill, order

from l2tca.book.types import Side
from l2tca.tca.analysis import (
    arrival_price,
    attribute_slippage,
    interval_vwap,
    simulate_child_orders,
)

pytestmark = pytest.mark.core

SECOND = 1_000_000_000


def views(n: int = 10, *, start_ns: int = 0, step_ns: int = SECOND):
    """A two-sided book whose *mid* drifts up by a cent per step.

    Both sides move together, so the spread stays at 1.00 and the mid walks
    100.50, 100.51, 100.52, ... A book whose two sides moved apart would leave
    the mid pinned and quietly make every benchmark in this file constant.
    """
    return [
        book_view(
            [(f"{100 + i * 0.01:.2f}", "10"), (f"{99 + i * 0.01:.2f}", "20")],
            [(f"{101 + i * 0.01:.2f}", "10"), (f"{102 + i * 0.01:.2f}", "20")],
            seq=i,
            recv_ns=start_ns + i * step_ns,
            recv_wall_ns=start_ns + i * step_ns,
        )
        for i in range(n)
    ]


def sparse_views():
    """The same book, but arriving twenty seconds apart. A quiet market is still one.

    ``views()`` delivers one view per second, which makes the instant something
    happened and the arrival time of the book that was standing then coincide.
    They do not coincide on a real feed, and a benchmark that confuses the two
    passes every test written on a regular grid.
    """
    return [
        book_view(
            [("100.00", "10"), ("99.00", "20")],
            [("101.00", "10"), ("102.00", "20")],
            seq=0,
            recv_ns=0,
            recv_wall_ns=0,
        ),
        book_view(
            [("100.50", "10"), ("99.50", "20")],
            [("101.50", "10"), ("102.50", "20")],
            seq=1,
            recv_ns=20 * SECOND,
            recv_wall_ns=20 * SECOND,
        ),
    ]


def two_futures(after_ns: int):
    """One shared past and two wildly different futures, both starting at ``after_ns``.

    Returns ``(past, rich, poor)``. Anything measured at an instant before
    ``after_ns`` has to give the same answer against ``past + rich`` and
    ``past + poor``, because at that instant the two worlds are identical.
    """
    past = [book_view([("100.00", "10")], [("101.00", "10")], seq=0, recv_ns=0)]
    rich = [book_view([("200.00", "10")], [("201.00", "10")], seq=1, recv_ns=after_ns)]
    poor = [book_view([("50.00", "10")], [("51.00", "10")], seq=1, recv_ns=after_ns)]
    return past, rich, poor


def outcome(call):
    """What a call did, in a form two calls can be compared by.

    A returned value, or the type of what was raised. Used where the test cares
    that two calls behave *the same*, without prescribing which behaviour --
    that is one of the decisions left to you.
    """
    try:
        return ("returned", call())
    except NotImplementedError:
        # Not a behaviour to compare. Without this the comparison is vacuously
        # true against the stubs, and the test goes green having checked nothing.
        raise
    except Exception as exc:
        return ("raised", type(exc))


# -- arrival benchmark -----------------------------------------------------


def test_arrival_price_lies_inside_some_book_in_the_window() -> None:
    """Whatever instant and price you pick, it has to come from the data."""
    series = views()
    price = arrival_price(order(decision_ns=3 * SECOND), series)
    assert isinstance(price, Decimal)
    assert min(v.bids[0].price for v in series) <= price <= max(v.asks[0].price for v in series)


def test_arrival_price_is_deterministic() -> None:
    series = views()
    parent = order(decision_ns=3 * SECOND)
    assert arrival_price(parent, series) == arrival_price(parent, series)


def test_arrival_price_moves_with_the_decision_instant() -> None:
    """The book drifts, so two decision times cannot share one benchmark."""
    series = views(20)
    early = arrival_price(order(decision_ns=1 * SECOND), series)
    late = arrival_price(order(decision_ns=15 * SECOND), series)
    assert early != late


def test_arrival_price_without_any_book_is_an_error() -> None:
    with pytest.raises(ValueError):
        arrival_price(order(), [])


# -- interval VWAP ---------------------------------------------------------


def test_interval_vwap_lies_within_the_window_prices() -> None:
    series = views(10)
    volumes = [(i * SECOND, Decimal("1")) for i in range(10)]
    price = interval_vwap(series, volumes, 0, 9 * SECOND)
    assert min(v.bids[0].price for v in series) <= price <= max(v.asks[0].price for v in series)


def test_interval_vwap_follows_where_the_volume_was() -> None:
    """A benchmark that ignores the volume distribution is not volume-weighted."""
    series = views(20)
    early = [(i * SECOND, Decimal("10") if i < 5 else Decimal("0")) for i in range(20)]
    late = [(i * SECOND, Decimal("0") if i < 15 else Decimal("10")) for i in range(20)]
    assert interval_vwap(series, early, 0, 19 * SECOND) != interval_vwap(
        series, late, 0, 19 * SECOND
    )


def test_interval_vwap_rejects_an_empty_window() -> None:
    with pytest.raises(ValueError):
        interval_vwap(views(), [(0, Decimal("1"))], 5 * SECOND, 5 * SECOND)


def test_interval_vwap_rejects_zero_total_volume() -> None:
    with pytest.raises(ValueError):
        interval_vwap(views(), [(0, Decimal("0"))], 0, 9 * SECOND)


# -- child order simulation ------------------------------------------------


def test_simulation_never_overfills_the_parent() -> None:
    parent = order(Side.BID, "10")
    fills = simulate_child_orders(parent, views(20), 0, 19 * SECOND)
    assert sum(f.qty for f in fills) <= parent.target_qty


def test_simulated_fills_stay_inside_the_window() -> None:
    """Deliberately on a book that arrives twice in forty seconds.

    On ``views()`` this assertion passes even if each fill is stamped with the
    arrival time of the book it read rather than the instant the child was sent,
    because on a one-per-second grid those are the same number. Here they are
    twenty seconds apart, so only one of the two can be inside the window.
    """
    fills = simulate_child_orders(order(Side.BID, "10"), sparse_views(), 2 * SECOND, 12 * SECOND)
    assert fills, "a book with depth on both sides should produce at least one fill"
    assert all(2 * SECOND <= f.ts_ns <= 12 * SECOND for f in fills)


def test_simulated_fills_are_ordered_and_positive() -> None:
    fills = simulate_child_orders(order(Side.BID, "10"), views(20), 0, 19 * SECOND)
    assert fills, "a liquid book over 20 views should produce at least one fill"
    assert [f.ts_ns for f in fills] == sorted(f.ts_ns for f in fills)
    assert all(f.qty > 0 and f.price > 0 for f in fills)


def test_a_buy_never_fills_below_the_best_ask_that_stood_at_the_time() -> None:
    """A simulator that fills through the offer is inventing liquidity."""
    series = views(20)
    fills = simulate_child_orders(order(Side.BID, "10"), series, 0, 19 * SECOND)
    for f in fills:
        standing = [v for v in series if v.recv_ns <= f.ts_ns]
        assert f.price >= standing[-1].asks[0].price


def test_simulation_over_an_empty_book_fills_nothing() -> None:
    empty = [book_view([], [], seq=i, recv_ns=i * SECOND) for i in range(5)]
    assert simulate_child_orders(order(Side.BID, "10"), empty, 0, 4 * SECOND) == []


# -- attribution -----------------------------------------------------------


def test_attribution_returns_named_finite_components() -> None:
    parent = order(Side.BID, "10")
    fills = [fill(2 * SECOND, "101.0", "5"), fill(4 * SECOND, "101.5", "5")]
    components = attribute_slippage(parent, fills, views(20))

    assert isinstance(components, dict)
    assert components, "an attribution with no components explains nothing"
    assert all(isinstance(k, str) for k in components)
    assert all(isinstance(v, float) and math.isfinite(v) for v in components.values())


def test_attribution_is_deterministic() -> None:
    parent = order(Side.BID, "10")
    fills = [fill(2 * SECOND, "101.0", "10")]
    series = views(20)
    assert attribute_slippage(parent, fills, series) == attribute_slippage(parent, fills, series)


def test_a_worse_execution_costs_more_somewhere() -> None:
    """Whatever the layers are, paying more has to show up in at least one."""
    parent = order(Side.BID, "10")
    series = views(20)
    cheap = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10")], series)
    dear = attribute_slippage(parent, [fill(2 * SECOND, "105.0", "10")], series)
    assert cheap.keys() == dear.keys()
    assert any(dear[k] > cheap[k] for k in dear)


def test_fees_reach_the_attribution() -> None:
    parent = order(Side.BID, "10")
    series = views(20)
    free = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10")], series)
    charged = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10", fee="5")], series)
    assert free != charged


def test_attribution_with_no_fills_still_returns_the_same_keys() -> None:
    """An unfilled order has a cost too; the shape of the answer should not change."""
    parent = order(Side.BID, "10")
    series = views(20)
    filled = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10")], series)
    unfilled = attribute_slippage(parent, [], series)
    assert filled.keys() == unfilled.keys()


# -- no look-ahead ---------------------------------------------------------
#
# Three tests for one rule, because it has to hold on every lookup rather than
# on the headline one. What an unavailable benchmark *does* is yours to decide
# (see question 1); that it cannot depend on a book which had not arrived is not,
# because such a number is unobtainable at the instant it claims to describe.


def test_arrival_price_cannot_see_a_book_that_had_not_arrived() -> None:
    past, rich, poor = two_futures(after_ns=9 * SECOND)
    parent = order(decision_ns=5 * SECOND)

    assert outcome(lambda: arrival_price(parent, past + rich)) == outcome(
        lambda: arrival_price(parent, past + poor)
    )
    # And with no past at all -- where falling back to the nearest view is most
    # tempting, and produces a number from a book nobody could have read.
    assert outcome(lambda: arrival_price(parent, rich)) == outcome(
        lambda: arrival_price(parent, poor)
    )


def test_interval_vwap_cannot_see_a_book_that_had_not_arrived() -> None:
    past, rich, poor = two_futures(after_ns=9 * SECOND)
    buckets = [(5 * SECOND, Decimal("1"))]

    assert outcome(lambda: interval_vwap(past + rich, buckets, 0, 8 * SECOND)) == outcome(
        lambda: interval_vwap(past + poor, buckets, 0, 8 * SECOND)
    )
    assert outcome(lambda: interval_vwap(rich, buckets, 0, 8 * SECOND)) == outcome(
        lambda: interval_vwap(poor, buckets, 0, 8 * SECOND)
    )


def test_attribution_cannot_see_a_book_that_had_not_arrived() -> None:
    past, rich, poor = two_futures(after_ns=9 * SECOND)
    parent = order(Side.BID, "10", decision_ns=5 * SECOND)
    fills = [fill(5 * SECOND, "101.0", "10")]

    assert outcome(lambda: attribute_slippage(parent, fills, past + rich)) == outcome(
        lambda: attribute_slippage(parent, fills, past + poor)
    )
    assert outcome(lambda: attribute_slippage(parent, fills, rich)) == outcome(
        lambda: attribute_slippage(parent, fills, poor)
    )
