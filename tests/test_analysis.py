"""Specification for :mod:`l2tca.tca.analysis`, as executable tests.

Two layers, written at different times and for different reasons.

**Properties**, first, while the three design questions at the top of
``l2tca/tca/analysis.py`` were still open -- which instant and which price make
the arrival benchmark, how a parent order is split and filled, how slippage
decomposes. A test asserting ``arrival_price(...) == 100.5`` would have answered
the first one, so what is pinned up there is only what must hold *whatever* the
answer turns out to be: a benchmark price lies inside the book that produced it,
a simulation cannot fill more than the parent quantity, a decomposition is finite
and internally consistent.

**Values**, second, once the decisions were made and recorded in ``docs/CORE.md``
section 4. These are arithmetic on a two-view book small enough to check by hand,
so a changed decision surfaces as a specific number moving rather than as a vague
failure.

The properties alone were not enough, and it is worth knowing why: they passed
over an implementation that stamped each fill with the arrival time of the book
it read rather than the instant the child was sent, because the fixture up here
delivers one view per second and the two coincide on that grid. The value tests
use uneven arrivals, where they do not.
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
    charged = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10", fee="5")], series)
    assert free != charged


def test_attribution_with_no_fills_still_returns_the_same_keys() -> None:
    """An unfilled order has a cost too; the shape of the answer should not change."""
    parent = order(Side.BID, "10")
    series = views(20)
    filled = attribute_slippage(parent, [fill(2 * SECOND, "101.0", "10")], series)
    unfilled = attribute_slippage(parent, [], series)
    assert filled.keys() == unfilled.keys()


# -- hand-computed values --------------------------------------------------
#
# Everything above asserts properties, which is what the specification could
# pin before the design decisions were made. The decisions are made (docs/CORE.md
# section 4), so these pin arithmetic instead: a changed decision now shows up as
# a specific number moving.
#
# The book both sections below use, chosen so the arrival mid is exactly 100 and
# a target notional of 1000 makes one currency unit worth exactly 10 bps:
#
#     view A, recv_ns = 0      bids 99.50 x5, 98.50 x10   asks 100.50 x3, 101.50 x7
#     view B, recv_ns = 10s    bids 100.50 x5, 99.50 x10  asks 101.50 x3, 102.50 x7
#
#     mid(A) = 100.00     mid(B) = 101.00     ask depth per view = 10


def two_views() -> list:
    return [
        book_view(
            [("99.50", "5"), ("98.50", "10")],
            [("100.50", "3"), ("101.50", "7")],
            seq=0,
            recv_ns=0,
            recv_wall_ns=0,
        ),
        book_view(
            [("100.50", "5"), ("99.50", "10")],
            [("101.50", "3"), ("102.50", "7")],
            seq=1,
            recv_ns=10 * SECOND,
            recv_wall_ns=10 * SECOND,
        ),
    ]


def test_arrival_price_takes_the_contemporaneous_view_not_the_next_one() -> None:
    series = two_views()
    # Between the two views: the later one had not arrived yet.
    assert arrival_price(order(decision_ns=9 * SECOND), series) == Decimal("100.00")
    # Exactly on a view: "at or before" includes at.
    assert arrival_price(order(decision_ns=10 * SECOND), series) == Decimal("101.00")


def test_arrival_price_before_every_view_does_not_reach_forward() -> None:
    """Taking the first view instead would be look-ahead, and would flatter the cost."""
    series = two_views()
    with pytest.raises(ValueError):
        arrival_price(order(decision_ns=-1), series)


def test_interval_vwap_weights_by_volume_not_by_view() -> None:
    series = two_views()
    volumes = [(0, Decimal("3")), (10 * SECOND, Decimal("1"))]
    # (3 * 100.00 + 1 * 101.00) / 4
    assert interval_vwap(series, volumes, 0, 10 * SECOND) == Decimal("100.25")


def test_interval_vwap_ignores_buckets_outside_the_window() -> None:
    series = two_views()
    volumes = [(0, Decimal("3")), (10 * SECOND, Decimal("1"))]
    # The second bucket falls outside, so only the first weighs in.
    assert interval_vwap(series, volumes, 0, 9 * SECOND) == Decimal("100.00")


def test_simulated_child_walks_the_book_one_fill_per_level() -> None:
    """A buy of 8 in two slices takes 3 at the touch and 1 behind it, twice."""
    fills = simulate_child_orders(order(Side.BID, "8"), two_views(), 0, 10 * SECOND, slices=2)
    assert [(f.ts_ns, f.price, f.qty) for f in fills] == [
        (0, Decimal("100.50"), Decimal("3")),
        (0, Decimal("101.50"), Decimal("1")),
        (5 * SECOND, Decimal("100.50"), Decimal("3")),
        (5 * SECOND, Decimal("101.50"), Decimal("1")),
    ]


def test_a_slice_larger_than_the_visible_book_carries_its_remainder() -> None:
    """30 wanted, 10 visible per slice: fill what is there, never extrapolate."""
    fills = simulate_child_orders(order(Side.BID, "30"), two_views(), 0, 10 * SECOND, slices=2)
    assert sum(f.qty for f in fills) == Decimal("20")  # 10 per slice, book exhausted
    assert max(f.price for f in fills) == Decimal("101.50")  # never past the last level


def test_fill_is_stamped_when_the_child_was_sent_not_when_the_book_arrived() -> None:
    """The two coincide only on a fixture with a regular grid. A real feed is not one."""
    sparse = [
        book_view([("99.50", "5")], [("100.50", "50")], seq=0, recv_ns=0),
        book_view([("99.50", "5")], [("100.50", "50")], seq=1, recv_ns=20 * SECOND),
    ]
    fills = simulate_child_orders(order(Side.BID, "10"), sparse, 5 * SECOND, 15 * SECOND)
    assert fills
    # Every child is worked at its scheduled instant, using the book standing then.
    assert all(5 * SECOND <= f.ts_ns < 15 * SECOND for f in fills)
    assert {f.ts_ns for f in fills} == {(5 + i) * SECOND for i in range(10)}


def test_attribution_decomposes_a_two_fill_execution_exactly() -> None:
    """Buy 10 from an arrival mid of 100, so 1.00 of cost is exactly 10 bps."""
    parent = order(Side.BID, "10", decision_ns=0)
    fills = [
        fill(0, "100.50", "4"),  # at the touch, mid 100.00 -> 0.50 of spread each
        fill(10 * SECOND, "102.50", "6", fee="3"),  # mid moved to 101.00 by now
    ]
    assert attribute_slippage(parent, fills, two_views()) == {
        "spread_bps": 110.0,  # (4 * 0.50 + 6 * 1.50) * 10
        "timing_bps": 60.0,  # (4 * 0.00 + 6 * 1.00) * 10
        "fees_bps": 30.0,  # 3.00 * 10
        "opportunity_bps": 0.0,  # fully filled
        "total_bps": 200.0,
    }


def test_unfilled_quantity_is_charged_at_the_close_of_the_window() -> None:
    """Underfilling while the market ran away is the cost shortfall exists to catch."""
    parent = order(Side.BID, "10", decision_ns=0)
    components = attribute_slippage(parent, [fill(0, "100.50", "4")], two_views())
    assert components == {
        "spread_bps": 20.0,  # 4 * 0.50 * 10
        "timing_bps": 0.0,
        "fees_bps": 0.0,
        "opportunity_bps": 60.0,  # 6 unfilled * (101.00 - 100.00) * 10
        "total_bps": 80.0,
    }


def test_a_sell_pays_a_positive_cost_too() -> None:
    """Sign convention: positive means cost on both sides, or sells look free."""
    parent = order(Side.ASK, "10", decision_ns=0)
    components = attribute_slippage(parent, [fill(0, "99.50", "10")], two_views())
    # Sold 0.50 below the arrival mid -- a cost, so positive.
    assert components["spread_bps"] == 50.0
    # And the market rose after, which for a seller who did fill is not a cost.
    assert components["timing_bps"] == 0.0


def test_the_layers_sum_to_the_total() -> None:
    """A decomposition with a residual is not one."""
    parent = order(Side.BID, "10", decision_ns=0)
    fills = [fill(0, "100.50", "4"), fill(10 * SECOND, "102.50", "3", fee="1")]
    components = attribute_slippage(parent, fills, two_views())
    layers = sum(v for k, v in components.items() if k != "total_bps")
    assert components["total_bps"] == pytest.approx(layers)


def test_attribution_will_not_reach_forward_for_a_missing_view() -> None:
    """Same rule as arrival_price, and it has to hold on every lookup, not just that one.

    Clamping a not-yet-arrived view into place is silent: it returns a plausible
    number built from a book the execution could not have seen.
    """
    series = two_views()
    parent = order(Side.BID, "10", decision_ns=0)

    with pytest.raises(ValueError):  # decision before any view
        attribute_slippage(order(Side.BID, "10", decision_ns=-1), [fill(0, "100.50", "10")], series)

    with pytest.raises(ValueError):  # a fill before any view
        attribute_slippage(parent, [fill(-1, "100.50", "10")], series)
