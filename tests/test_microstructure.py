"""Specification for :mod:`l2tca.signals.microstructure`, as executable tests.

FAILS until the factors are written.

The reference book used throughout::

    bids: 100.0 x 10, 99.0 x 20
    asks: 101.0 x  5, 102.0 x 15
    mid = 100.5, quoted spread = 1.0
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from tests.factories import book_view

from l2tca.book.types import Side
from l2tca.signals.microstructure import (
    effective_spread,
    micro_price,
    order_book_imbalance,
    quoted_spread,
)

pytestmark = pytest.mark.core

BIDS = [("100.0", "10"), ("99.0", "20")]
ASKS = [("101.0", "5"), ("102.0", "15")]


@pytest.fixture
def view():
    return book_view(BIDS, ASKS)


@pytest.fixture
def empty_view():
    return book_view([], [])


# -- imbalance -------------------------------------------------------------


def test_top_of_book_imbalance(view) -> None:
    assert order_book_imbalance(view, levels=1) == pytest.approx((10 - 5) / 15)


def test_imbalance_sums_over_the_requested_depth(view) -> None:
    assert order_book_imbalance(view, levels=2) == pytest.approx((30 - 20) / 50)


def test_imbalance_is_bounded_and_signed() -> None:
    assert order_book_imbalance(book_view([("100", "10")], [])) == pytest.approx(1.0)
    assert order_book_imbalance(book_view([], [("101", "10")])) == pytest.approx(-1.0)


def test_a_balanced_book_has_zero_imbalance() -> None:
    assert order_book_imbalance(book_view([("100", "7")], [("101", "7")])) == pytest.approx(0.0)


def test_imbalance_uses_what_exists_when_the_side_is_thin(view) -> None:
    assert order_book_imbalance(view, levels=50) == pytest.approx((30 - 20) / 50)


def test_imbalance_of_an_empty_book_is_not_a_number(empty_view) -> None:
    assert math.isnan(order_book_imbalance(empty_view))


# -- micro price -----------------------------------------------------------


def test_micro_price_weights_each_price_by_the_opposite_size(view) -> None:
    assert micro_price(view) == pytest.approx((100.0 * 5 + 101.0 * 10) / 15)


def test_micro_price_reduces_to_the_mid_when_sizes_match() -> None:
    assert micro_price(book_view([("100", "4")], [("102", "4")])) == pytest.approx(101.0)


def test_micro_price_leans_toward_the_thin_side() -> None:
    assert micro_price(book_view([("100", "1")], [("101", "100")])) < 100.5
    assert micro_price(book_view([("100", "100")], [("101", "1")])) > 100.5


def test_micro_price_on_a_one_sided_book_is_not_a_number() -> None:
    assert math.isnan(micro_price(book_view([("100.0", "1")], [])))


# -- quoted spread ---------------------------------------------------------


def test_quoted_spread_in_absolute_terms(view) -> None:
    assert quoted_spread(view, in_bps=False) == pytest.approx(1.0)


def test_quoted_spread_in_basis_points(view) -> None:
    assert quoted_spread(view) == pytest.approx(1e4 * 1.0 / 100.5)


def test_quoted_spread_of_an_empty_book_is_not_a_number(empty_view) -> None:
    assert math.isnan(quoted_spread(empty_view))


# -- effective spread ------------------------------------------------------


def test_paying_the_touch_gives_an_effective_spread_equal_to_the_quoted_one(view) -> None:
    assert effective_spread(view, Decimal("101.0"), Side.BID) == pytest.approx(quoted_spread(view))


def test_a_midpoint_fill_has_zero_effective_spread(view) -> None:
    assert effective_spread(view, Decimal("100.5"), Side.BID) == pytest.approx(0.0)


def test_effective_spread_is_symmetric_across_sides(view) -> None:
    """Positive means "paid away from the mid" on both sides of the market."""
    buy = effective_spread(view, Decimal("101.0"), Side.BID)
    sell = effective_spread(view, Decimal("100.0"), Side.ASK)
    assert buy == pytest.approx(sell)
    assert buy > 0


def test_price_improvement_is_negative(view) -> None:
    assert effective_spread(view, Decimal("100.2"), Side.BID) < 0


def test_walking_the_book_costs_more_than_the_quoted_spread(view) -> None:
    assert effective_spread(view, Decimal("102.0"), Side.BID) > quoted_spread(view)


def test_effective_spread_in_absolute_terms(view) -> None:
    assert effective_spread(view, Decimal("101.0"), Side.BID, in_bps=False) == pytest.approx(1.0)


def test_effective_spread_without_a_mid_is_not_a_number(empty_view) -> None:
    assert math.isnan(effective_spread(empty_view, Decimal("100.0"), Side.BID))


# -- purity ----------------------------------------------------------------


def test_factors_are_pure_functions_of_the_view(view) -> None:
    """Same view in, same number out -- otherwise replay proves nothing."""
    assert order_book_imbalance(view, 2) == order_book_imbalance(view, 2)
    assert micro_price(view) == micro_price(view)
    assert quoted_spread(view) == quoted_spread(view)
