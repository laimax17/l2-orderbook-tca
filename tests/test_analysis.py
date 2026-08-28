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
    """A drifting two-sided book, one view per step."""
    return [
        book_view(
            [(f"{100 - i * 0.01:.2f}", "10"), (f"{99 - i * 0.01:.2f}", "20")],
            [(f"{101 + i * 0.01:.2f}", "10"), (f"{102 + i * 0.01:.2f}", "20")],
            seq=i,
            recv_ns=start_ns + i * step_ns,
            recv_wall_ns=start_ns + i * step_ns,
        )
        for i in range(n)
    ]


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
    fills = simulate_child_orders(order(Side.BID, "10"), views(20), 2 * SECOND, 12 * SECOND)
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
    charged = attribute_slippage(
        parent, [fill(2 * SECOND, "101.0", "10", fee="5")], series
    )
    assert free != charged


def test_attribution_with_no_fills_still_returns_the_same_keys() -> None:
    """An unfilled order has a cost too; the shape of the answer should not change."""
    parent = order(Side.BID, "10")
    series = views(20)
    filled = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10")], series)
    unfilled = attribute_slippage(parent, [], series)
    assert filled.keys() == unfilled.keys()
