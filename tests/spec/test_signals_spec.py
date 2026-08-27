"""Executable specification for :mod:`l2tca.signals.microstructure`."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from tests.spec.marks import unimplemented

from l2tca.book.base import BookView, Level
from l2tca.signals.microstructure import (
    book_pressure,
    depth_slope,
    log_depth_ratio,
    micro_price,
    order_book_imbalance,
    relative_spread_bps,
    weighted_mid,
)


def D(v: str) -> Decimal:
    return Decimal(v)


def make_view(bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> BookView:
    return BookView(
        "BTC/USD", 1, 0, 0, None,
        tuple(Level(D(p), D(q)) for p, q in bids),
        tuple(Level(D(p), D(q)) for p, q in asks),
    )


# -- imbalance -------------------------------------------------------------


@unimplemented
def test_top_of_book_imbalance(view: BookView) -> None:
    # bid 10 vs ask 5 -> (10 - 5) / 15
    assert order_book_imbalance(view, levels=1) == pytest.approx(5 / 15)


@unimplemented
def test_imbalance_sums_over_the_requested_depth(view: BookView) -> None:
    # bids 10+20 = 30, asks 5+15 = 20
    assert order_book_imbalance(view, levels=2) == pytest.approx(10 / 50)


@unimplemented
def test_imbalance_is_bounded_and_signed() -> None:
    all_bid = make_view([("100", "10")], [])
    all_ask = make_view([], [("101", "10")])
    assert order_book_imbalance(all_bid) == pytest.approx(1.0)
    assert order_book_imbalance(all_ask) == pytest.approx(-1.0)


@unimplemented
def test_a_balanced_book_has_zero_imbalance() -> None:
    assert order_book_imbalance(make_view([("100", "7")], [("101", "7")])) == pytest.approx(0.0)


@unimplemented
def test_imbalance_uses_what_exists_when_the_side_is_thin(view: BookView) -> None:
    assert not math.isnan(order_book_imbalance(view, levels=50))


@unimplemented
def test_imbalance_of_an_empty_book_is_nan(empty_view: BookView) -> None:
    """nan, not zero: zero is a real, balanced book and reads as a signal."""
    assert math.isnan(order_book_imbalance(empty_view))


# -- micro price -----------------------------------------------------------


@unimplemented
def test_micro_price_weights_each_price_by_the_opposite_size(view: BookView) -> None:
    # (100 * 5 + 101 * 10) / 15 -- the heavy bid pulls the fair price toward the ask
    assert micro_price(view) == pytest.approx((100.0 * 5 + 101.0 * 10) / 15)


@unimplemented
def test_micro_price_reduces_to_the_mid_when_sizes_match() -> None:
    assert micro_price(make_view([("100", "4")], [("102", "4")])) == pytest.approx(101.0)


@unimplemented
def test_micro_price_moves_toward_the_thin_side() -> None:
    """A large resting ask means the next trade likelier hits the bid."""
    heavy_ask = make_view([("100", "1")], [("101", "100")])
    assert micro_price(heavy_ask) < 100.5


@unimplemented
def test_micro_price_on_a_one_sided_book_is_nan(one_sided_view: BookView) -> None:
    assert math.isnan(micro_price(one_sided_view))


# -- other factors ---------------------------------------------------------


@unimplemented
def test_weighted_mid_averages_the_two_sides(view: BookView) -> None:
    bid_vwap = (100 * 10 + 99 * 20) / 30
    ask_vwap = (101 * 5 + 102 * 15) / 20
    assert weighted_mid(view, levels=2) == pytest.approx((bid_vwap + ask_vwap) / 2)


@unimplemented
def test_relative_spread_is_in_basis_points_of_the_mid(view: BookView) -> None:
    assert relative_spread_bps(view) == pytest.approx(1e4 * 1.0 / 100.5)


@unimplemented
def test_relative_spread_of_an_empty_book_is_nan(empty_view: BookView) -> None:
    assert math.isnan(relative_spread_bps(empty_view))


@unimplemented
def test_log_depth_ratio_is_symmetric_around_zero() -> None:
    heavy_bid = make_view([("100", "20")], [("101", "10")])
    heavy_ask = make_view([("100", "10")], [("101", "20")])
    assert log_depth_ratio(heavy_bid) == pytest.approx(math.log(2))
    assert log_depth_ratio(heavy_ask) == pytest.approx(-math.log(2))


@unimplemented
def test_book_pressure_discounts_depth_far_from_the_touch() -> None:
    """Quantity resting far away is cheap to post; counting it at face value is spoofable."""
    near = make_view([("100", "10"), ("99.9", "10")], [("101", "10"), ("101.1", "10")])
    far = make_view([("100", "10"), ("50", "1000")], [("101", "10"), ("151", "10")])
    assert book_pressure(near, levels=2) == pytest.approx(0.0, abs=1e-9)
    assert book_pressure(far, levels=2) < order_book_imbalance(far, levels=2)


@unimplemented
def test_depth_slope_is_steeper_for_a_more_resilient_book() -> None:
    thick = make_view([("100", "10"), ("99", "50"), ("98", "90")], [("101", "1")])
    thin = make_view([("100", "1"), ("99", "2"), ("98", "3")], [("101", "1")])
    assert depth_slope(thick, "bid", levels=3) > depth_slope(thin, "bid", levels=3)


@unimplemented
def test_depth_slope_needs_at_least_two_levels(one_sided_view: BookView) -> None:
    assert math.isnan(depth_slope(one_sided_view, "bid", levels=10))


@unimplemented
def test_factors_are_pure_functions_of_the_view(view: BookView) -> None:
    """Same view in, same number out -- otherwise replay proves nothing."""
    assert order_book_imbalance(view, 3) == order_book_imbalance(view, 3)
    assert micro_price(view) == micro_price(view)
