"""Signal evaluation, pinned on frames small enough to check by hand."""

from __future__ import annotations

import polars as pl
import pytest

from l2tca.research import (
    bucket_summary,
    forward_return_bps,
    information_coefficient,
    signals_wide,
)

SECOND = 1_000_000_000


def series(mids: list[float], *, step_ns: int = SECOND) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "recv_ns": [i * step_ns for i in range(len(mids))],
            "mid": mids,
            "signal": [float(i) for i in range(len(mids))],
        }
    )


def test_forward_return_is_measured_in_time_not_in_rows() -> None:
    """A busy feed and a quiet one are the same market; 'ten rows ahead' is not."""
    busy = pl.DataFrame({"recv_ns": [0, SECOND // 10, SECOND], "mid": [100.0, 100.0, 101.0]})
    out = forward_return_bps(busy, SECOND)
    # From t=0 the mid one second later is 101, whatever happened in between.
    assert out.get_column("forward_bps")[0] == pytest.approx(100.0)


def test_a_flat_market_returns_zero() -> None:
    """Four of the five rows, not five: the last has no second to look forward into."""
    out = forward_return_bps(series([100.0] * 5), SECOND)
    assert out.get_column("forward_bps").to_list() == [0.0, 0.0, 0.0, 0.0, None]


def test_the_move_is_signed_and_in_basis_points() -> None:
    out = forward_return_bps(series([100.0, 101.0, 100.0]), SECOND)
    got = out.get_column("forward_bps").to_list()
    assert got[0] == pytest.approx(100.0)  # +1 on 100 is 100 bps
    assert got[1] == pytest.approx(-1 / 101 * 10_000)


def test_rows_without_a_full_horizon_ahead_are_null_not_clipped() -> None:
    """Clipping shortens the horizon exactly where a trend has most likely ended."""
    out = forward_return_bps(series([100.0, 101.0, 102.0]), 2 * SECOND)
    got = out.get_column("forward_bps").to_list()
    assert got[0] == pytest.approx(200.0)
    assert got[1:] == [None, None]


def test_a_non_positive_horizon_is_an_error() -> None:
    with pytest.raises(ValueError, match="horizon must be positive"):
        forward_return_bps(series([100.0, 101.0]), 0)


def test_buckets_are_quantiles_so_a_narrow_signal_still_spreads() -> None:
    """Equal-width bins would put all but one observation in a single bin."""
    frame = pl.DataFrame(
        {"signal": [0.0, 1e-9, 2e-9, 3e-9, 100.0], "forward_bps": [1.0, 2.0, 3.0, 4.0, 5.0]}
    )
    table = bucket_summary(frame, "signal", buckets=5)
    assert table.get_column("n").to_list() == [1, 1, 1, 1, 1]


def test_bucket_means_follow_a_signal_that_predicts() -> None:
    frame = pl.DataFrame(
        {"signal": list(range(100)), "forward_bps": [float(i) for i in range(100)]}
    )
    table = bucket_summary(frame, "signal", buckets=10)
    means = table.get_column("forward_mean").to_list()
    assert means == sorted(means)
    assert table.get_column("n").to_list() == [10] * 10
    assert "forward_stderr" in table.columns


def test_bucket_summary_drops_rows_missing_either_column() -> None:
    frame = pl.DataFrame({"signal": [1.0, 2.0, None, 4.0], "forward_bps": [1.0, None, 3.0, 4.0]})
    assert bucket_summary(frame, "signal", buckets=2).get_column("n").sum() == 2


def test_bucket_summary_refuses_an_empty_overlap() -> None:
    frame = pl.DataFrame({"signal": [1.0, 2.0], "forward_bps": [None, None]})
    with pytest.raises(ValueError, match="no rows"):
        bucket_summary(frame, "signal")


def test_information_coefficient_is_rank_based() -> None:
    """A monotone but wildly non-linear relationship is still a perfect ranking."""
    frame = pl.DataFrame(
        {"signal": [1.0, 2.0, 3.0, 4.0], "forward_bps": [1.0, 4.0, 9.0, 1_000_000.0]}
    )
    assert information_coefficient(frame, "signal") == pytest.approx(1.0)


def test_information_coefficient_sees_an_inverted_signal() -> None:
    frame = pl.DataFrame({"signal": [1.0, 2.0, 3.0, 4.0], "forward_bps": [4.0, 3.0, 2.0, 1.0]})
    assert information_coefficient(frame, "signal") == pytest.approx(-1.0)


def test_information_coefficient_needs_observations() -> None:
    frame = pl.DataFrame({"signal": [1.0, 2.0], "forward_bps": [1.0, None]})
    with pytest.raises(ValueError, match="at least three"):
        information_coefficient(frame, "signal")


def test_signals_wide_keeps_each_depth_apart() -> None:
    """imbalance at one level and at five are different factors, not one column twice."""
    long = pl.DataFrame(
        {
            "book_seq": [1, 1, 1, 2, 2, 2],
            "recv_ns": [0, 0, 0, SECOND, SECOND, SECOND],
            "name": ["mid", "imbalance", "imbalance"] * 2,
            "levels": [1, 1, 5] * 2,
            "value": [100.0, 0.5, 0.1, 101.0, -0.5, -0.1],
        }
    )
    wide = signals_wide(long)
    assert wide.height == 2
    assert {"mid", "imbalance_1", "imbalance_5"} <= set(wide.columns)
    assert wide.get_column("imbalance_1").to_list() == [0.5, -0.5]
    assert wide.get_column("imbalance_5").to_list() == [0.1, -0.1]
