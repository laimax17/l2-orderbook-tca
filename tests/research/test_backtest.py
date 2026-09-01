"""The execution backtest: a TWAP worked across windows, priced against benchmarks."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from tests.factories import book_view, order

from l2tca.book.types import Side
from l2tca.research.backtest import run_window, run_windows

FIXTURE = Path("tests/fixtures/sample.jsonl.gz")
SECOND = 1_000_000_000


def flat_views(n: int = 10):
    """A still book: bid 99.00 / ask 101.00, so the mid is 100 and the touch is 1.00."""
    return [
        book_view([("99.00", "100")], [("101.00", "100")], seq=i, recv_ns=i * SECOND)
        for i in range(n)
    ]


def test_a_buy_on_a_still_book_pays_exactly_the_half_spread() -> None:
    """Arrival is the mid; every child lifts the offer. 1.00 above 100 is 100 bps."""
    row = run_window(
        order(Side.BID, "10", decision_ns=0), flat_views(), [], 0, 9 * SECOND, slices=5
    )
    assert row["filled_qty"] == 10.0
    assert row["avg_price"] == pytest.approx(101.0)
    assert row["arrival"] == pytest.approx(100.0)
    assert row["vs_arrival_bps"] == pytest.approx(100.0)


def test_a_sell_is_measured_with_the_same_sign() -> None:
    row = run_window(
        order(Side.ASK, "10", decision_ns=0), flat_views(), [], 0, 9 * SECOND, slices=5
    )
    assert row["avg_price"] == pytest.approx(99.0)
    assert row["vs_arrival_bps"] == pytest.approx(100.0)  # a cost, not a gain


def test_a_window_with_no_trades_reports_no_vwap_rather_than_a_number() -> None:
    """The alternative is quietly substituting the mid, which is a different benchmark."""
    row = run_window(order(Side.BID, "1", decision_ns=0), flat_views(), [], 0, 9 * SECOND)
    assert row["interval_vwap"] is None
    assert row["vs_vwap_bps"] is None


def test_traded_volume_reaches_the_vwap_benchmark() -> None:
    volumes = [(i * SECOND, Decimal("1")) for i in range(10)]
    row = run_window(order(Side.BID, "1", decision_ns=0), flat_views(), volumes, 0, 9 * SECOND)
    assert row["interval_vwap"] == pytest.approx(100.0)  # every bucket sees the same mid
    assert row["vs_vwap_bps"] == pytest.approx(100.0)


def test_the_attribution_layers_come_through() -> None:
    row = run_window(order(Side.BID, "10", decision_ns=0), flat_views(), [], 0, 9 * SECOND)
    layers = ["spread_bps", "timing_bps", "fees_bps", "opportunity_bps", "total_bps"]
    assert all(k in row for k in layers)
    assert row["total_bps"] == pytest.approx(sum(row[k] for k in layers[:-1]))


@pytest.mark.core
def test_windows_are_spread_across_the_capture_and_all_fill() -> None:
    rows = list(run_windows(FIXTURE, qty=Decimal("2"), windows=5, duration_ns=20 * SECOND))
    assert len(rows) == 5
    assert all(r["filled_qty"] == 2.0 for r in rows)
    starts = [r["start_ns"] for r in rows]
    assert starts == sorted(starts)
    assert starts[-1] > starts[0]


@pytest.mark.core
def test_the_backtest_is_deterministic() -> None:
    """Same capture, same schedule, same answer -- the point of replaying from a file."""
    kwargs = {"qty": Decimal("2"), "windows": 4, "duration_ns": 20 * SECOND}
    assert list(run_windows(FIXTURE, **kwargs)) == list(run_windows(FIXTURE, **kwargs))


@pytest.mark.core
def test_a_window_longer_than_the_capture_is_an_error() -> None:
    with pytest.raises(ValueError, match="shorter than one"):
        list(run_windows(FIXTURE, duration_ns=3600 * SECOND))


def test_zero_windows_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least one window"):
        list(run_windows(FIXTURE, windows=0))
