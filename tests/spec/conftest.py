"""Book fixtures shared by the specification suites."""

from __future__ import annotations

from decimal import Decimal

import pytest

from l2tca.book.base import BookView, Level


def D(value: str) -> Decimal:
    return Decimal(value)


@pytest.fixture
def view() -> BookView:
    """A small, balanced book: 1 tick wide, 4 levels a side, easy to reason about.

    bids: 100.0 x 10, 99.0 x 20, 98.0 x 30, 97.0 x 40
    asks: 101.0 x 5,  102.0 x 15, 103.0 x 25, 104.0 x 35
    mid = 100.5, spread = 1.0
    """
    return BookView(
        symbol="BTC/USD",
        seq=1,
        recv_ns=1_000,
        recv_wall_ns=2_000,
        exchange_ts_ns=None,
        bids=(
            Level(D("100.0"), D("10")),
            Level(D("99.0"), D("20")),
            Level(D("98.0"), D("30")),
            Level(D("97.0"), D("40")),
        ),
        asks=(
            Level(D("101.0"), D("5")),
            Level(D("102.0"), D("15")),
            Level(D("103.0"), D("25")),
            Level(D("104.0"), D("35")),
        ),
    )


@pytest.fixture
def empty_view() -> BookView:
    return BookView("BTC/USD", 0, 0, 0, None, (), ())


@pytest.fixture
def one_sided_view() -> BookView:
    return BookView("BTC/USD", 0, 0, 0, None, (Level(D("100.0"), D("1")),), ())
