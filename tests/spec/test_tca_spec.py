"""Executable specification for :mod:`l2tca.tca.execution`."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from tests.spec.marks import unimplemented

from l2tca.book.base import BookView, Level, Side
from l2tca.tca.base import Fill, Order
from l2tca.tca.execution import (
    arrival_slippage_bps,
    effective_spread_bps,
    implementation_shortfall_bps,
    realized_spread_bps,
    twap_benchmark,
    walk_the_book_cost,
)


def D(v: str) -> Decimal:
    return Decimal(v)


def make_view(bids, asks, recv_ns: int = 0) -> BookView:
    return BookView(
        "BTC/USD", 1, recv_ns, 0, None,
        tuple(Level(D(p), D(q)) for p, q in bids),
        tuple(Level(D(p), D(q)) for p, q in asks),
    )


@pytest.fixture
def buy_order() -> Order:
    return Order("BTC/USD", Side.BID, D("10"), arrival_ns=1_000, order_id="o1")


@pytest.fixture
def sell_order() -> Order:
    return Order("BTC/USD", Side.ASK, D("10"), arrival_ns=1_000, order_id="o2")


# -- sign convention -------------------------------------------------------


@unimplemented
def test_a_buy_above_arrival_is_a_positive_cost(buy_order: Order) -> None:
    fills = [Fill(2_000, D("101.0"), D("10"))]
    assert arrival_slippage_bps(buy_order, fills, D("100.0")) == pytest.approx(100.0)


@unimplemented
def test_a_sell_below_arrival_is_also_a_positive_cost(sell_order: Order) -> None:
    """Positive means cost on both sides. Getting this wrong flatters half the book."""
    fills = [Fill(2_000, D("99.0"), D("10"))]
    assert arrival_slippage_bps(sell_order, fills, D("100.0")) == pytest.approx(100.0)


@unimplemented
def test_price_improvement_is_negative(buy_order: Order) -> None:
    fills = [Fill(2_000, D("99.5"), D("10"))]
    assert arrival_slippage_bps(buy_order, fills, D("100.0")) < 0


@unimplemented
def test_slippage_is_quantity_weighted_not_a_simple_mean(buy_order: Order) -> None:
    fills = [Fill(2_000, D("100.0"), D("9")), Fill(2_100, D("110.0"), D("1"))]
    # average price 101.0, not 105.0
    assert arrival_slippage_bps(buy_order, fills, D("100.0")) == pytest.approx(100.0)


@unimplemented
def test_slippage_with_no_fills_is_nan(buy_order: Order) -> None:
    assert math.isnan(arrival_slippage_bps(buy_order, [], D("100.0")))


# -- implementation shortfall ---------------------------------------------


@unimplemented
def test_shortfall_includes_fees(buy_order: Order) -> None:
    without = implementation_shortfall_bps(
        buy_order, [Fill(2_000, D("100.0"), D("10"))], D("100.0")
    )
    with_fee = implementation_shortfall_bps(
        buy_order, [Fill(2_000, D("100.0"), D("10"), fee=D("1.0"))], D("100.0")
    )
    assert with_fee > without


@unimplemented
def test_shortfall_denominator_is_target_notional_not_filled(buy_order: Order) -> None:
    """Dividing by filled notional makes a badly underfilled order look cheap."""
    half = [Fill(2_000, D("101.0"), D("5"))]
    # 5 units * 1.0 cost / (10 units * 100.0) = 50 bps, not 100.
    assert implementation_shortfall_bps(buy_order, half, D("100.0")) == pytest.approx(50.0)


@unimplemented
def test_shortfall_charges_opportunity_cost_on_the_unfilled_remainder(buy_order: Order) -> None:
    fills = [Fill(2_000, D("100.0"), D("5"))]
    priced = implementation_shortfall_bps(buy_order, fills, D("100.0"), final_mid=D("110.0"))
    unpriced = implementation_shortfall_bps(buy_order, fills, D("100.0"))
    assert priced > unpriced, "not trading while the price ran away is a real cost"


@unimplemented
def test_shortfall_with_a_degenerate_benchmark_is_nan(buy_order: Order) -> None:
    assert math.isnan(
        implementation_shortfall_bps(buy_order, [Fill(1, D("1"), D("1"))], D("0"))
    )


# -- spreads ---------------------------------------------------------------


@unimplemented
def test_paying_the_touch_gives_an_effective_spread_equal_to_the_quoted_one() -> None:
    view = make_view([("99.0", "10")], [("101.0", "10")])  # mid 100, quoted 2.0
    fill = Fill(1_000, D("101.0"), D("1"))
    assert effective_spread_bps(fill, view, Side.BID) == pytest.approx(1e4 * 2.0 / 100.0)


@unimplemented
def test_a_midpoint_fill_has_zero_effective_spread() -> None:
    view = make_view([("99.0", "10")], [("101.0", "10")])
    fill = Fill(1_000, D("100.0"), D("1"))
    assert effective_spread_bps(fill, view, Side.BID) == pytest.approx(0.0)


@unimplemented
def test_effective_spread_is_symmetric_across_sides() -> None:
    view = make_view([("99.0", "10")], [("101.0", "10")])
    buy = effective_spread_bps(Fill(1, D("101.0"), D("1")), view, Side.BID)
    sell = effective_spread_bps(Fill(1, D("99.0"), D("1")), view, Side.ASK)
    assert buy == pytest.approx(sell)


@unimplemented
def test_realized_spread_is_smaller_when_the_price_keeps_moving() -> None:
    """Effective = realized + 2 * impact. An informed buy keeps most of its cost."""
    fill = Fill(1_000, D("101.0"), D("1"))
    at_fill = make_view([("99.0", "10")], [("101.0", "10")])
    moved_up = make_view([("100.5", "10")], [("102.5", "10")], recv_ns=61_000_000_000)

    effective = effective_spread_bps(fill, at_fill, Side.BID)
    realized = realized_spread_bps(fill, moved_up, Side.BID)
    assert realized < effective


# -- book walk -------------------------------------------------------------


@unimplemented
def test_walking_the_book_averages_the_consumed_levels() -> None:
    view = make_view([("99", "10")], [("100", "1"), ("101", "1"), ("102", "1")])
    avg, worst, complete = walk_the_book_cost(view, Side.BID, D("2"))
    assert avg == pytest.approx(D("100.5"))
    assert worst == D("101")
    assert complete


@unimplemented
def test_a_buy_consumes_asks_and_a_sell_consumes_bids() -> None:
    view = make_view([("99", "5"), ("98", "5")], [("100", "5"), ("101", "5")])
    buy_avg, _, _ = walk_the_book_cost(view, Side.BID, D("5"))
    sell_avg, _, _ = walk_the_book_cost(view, Side.ASK, D("5"))
    assert buy_avg == D("100")
    assert sell_avg == D("99")


@unimplemented
def test_a_thin_book_reports_a_partial_fill_without_inventing_a_price() -> None:
    """An extrapolated price for the unfillable remainder is a fabricated number."""
    view = make_view([("99", "1")], [("100", "1")])
    avg, _, complete = walk_the_book_cost(view, Side.BID, D("100"))
    assert not complete
    assert avg == D("100"), "average over the filled portion only"


@unimplemented
def test_walking_a_non_positive_quantity_is_an_error() -> None:
    view = make_view([("99", "1")], [("100", "1")])
    with pytest.raises(ValueError):
        walk_the_book_cost(view, Side.BID, D("0"))


# -- benchmarks ------------------------------------------------------------


@unimplemented
def test_twap_weights_by_time_not_by_update_count() -> None:
    """Updates arrive in bursts; an unweighted mean is event-weighted, not time-weighted."""
    views = [
        make_view([("99", "1")], [("101", "1")], recv_ns=0),          # mid 100, stands 9s
        make_view([("199", "1")], [("201", "1")], recv_ns=9_000_000_000),   # mid 200, 0.5s
        make_view([("199", "1")], [("201", "1")], recv_ns=9_500_000_000),   # mid 200, 0.5s
    ]
    result = twap_benchmark(views, 0, 10_000_000_000)
    assert result == pytest.approx(D("110"))  # 0.9 * 100 + 0.1 * 200
    assert result != pytest.approx(D("166.67"), abs=D("1"))  # the unweighted mean


@unimplemented
def test_twap_rejects_an_empty_window() -> None:
    views = [make_view([("99", "1")], [("101", "1")], recv_ns=0)]
    with pytest.raises(ValueError):
        twap_benchmark(views, 1_000, 1_000)


@unimplemented
def test_twap_needs_at_least_one_two_sided_book(empty_view: BookView) -> None:
    with pytest.raises(ValueError):
        twap_benchmark([empty_view], 0, 1_000)
