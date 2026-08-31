"""The producer for the snapshot and signal tables, which needs a working book."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from tests.conftest import SNAPSHOT_FRAME, UPDATE_FRAME
from tests.factories import book_view, raw_messages, write_capture

from l2tca.cli import main
from l2tca.io.derive import (
    iter_book_views,
    iter_derived_rows,
    signal_rows,
    snapshot_row,
)
from l2tca.io.reader import read_table

FIXTURE = Path("tests/fixtures/sample.jsonl.gz")


def test_updates_before_the_snapshot_are_skipped(tmp_path: Path) -> None:
    """There is no book to apply them to, and inventing one fabricates depth."""
    path = write_capture(
        tmp_path / "late.jsonl", raw_messages([UPDATE_FRAME, SNAPSHOT_FRAME, UPDATE_FRAME])
    )
    views = list(iter_book_views(path))
    assert len(views) == 2  # the snapshot and the update after it, not the one before


def test_snapshot_row_lists_stay_parallel() -> None:
    view = book_view([("100.0", "3"), ("99.0", "4")], [("101.0", "5")], seq=7)
    row = snapshot_row(view)
    assert row["book_seq"] == 7
    assert row["bid_px"] == [100.0, 99.0]
    assert len(row["bid_px"]) == len(row["bid_qty"])
    assert len(row["ask_px"]) == len(row["ask_qty"]) == 1


def test_signal_rows_need_both_sides() -> None:
    """Every factor here is defined against both; a zero would be a value the book never had."""
    assert signal_rows(book_view([("100.0", "3")], [])) == []
    assert signal_rows(book_view([], [("101.0", "3")])) == []
    names = {r["name"] for r in signal_rows(book_view([("100.0", "3")], [("101.0", "1")]))}
    assert names == {"mid", "micro_price", "quoted_spread_bps", "imbalance"}


def test_imbalance_is_reported_at_each_requested_depth() -> None:
    view = book_view(
        [(f"{100 - i}", "1") for i in range(5)], [(f"{101 + i}", "1") for i in range(5)]
    )
    rows = signal_rows(view, imbalance_levels=(1, 3, 5))
    levels = {r["levels"] for r in rows if r["name"] == "imbalance"}
    assert levels == {1, 3, 5}
    # A depth the book cannot supply is skipped rather than reported short.
    shallow = signal_rows(book_view([("100", "1")], [("101", "1")]), imbalance_levels=(1, 5))
    assert {r["levels"] for r in shallow if r["name"] == "imbalance"} == {1}


@pytest.mark.core
def test_every_frame_of_the_real_capture_is_verified() -> None:
    """The strongest check available: the exchange's own ledger, on every derived row."""
    verdicts = [view.checksum_ok for _m, view in iter_book_views(FIXTURE)]
    assert verdicts[0] is None  # the opening snapshot carries no update checksum
    assert set(verdicts[1:]) == {True}
    assert len(verdicts) == 4853


@pytest.mark.core
def test_signal_values_match_an_independent_recomputation() -> None:
    """Imbalance recomputed from the stored depth, rather than trusting the writer."""
    for _message, view in iter_book_views(FIXTURE, limit=200):
        rows = {(r["name"], r["levels"]): r["value"] for r in signal_rows(view)}
        qb, qa = float(view.bids[0].qty), float(view.asks[0].qty)
        assert rows[("imbalance", 1)] == pytest.approx((qb - qa) / (qb + qa))
        assert rows[("mid", 1)] == pytest.approx(
            (float(view.bids[0].price) + float(view.asks[0].price)) / 2
        )


@pytest.mark.core
def test_cli_writes_both_tables(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["signals", str(FIXTURE), "--out", str(tmp_path), "--limit", "500"]) == 0
    out = capsys.readouterr().out
    assert "snapshot rows" in out and "signal rows" in out

    snapshots = read_table(tmp_path, "snapshot")
    signals = read_table(tmp_path, "signal")
    assert snapshots.height > 0
    # One snapshot row per applied frame; several signal rows per snapshot row.
    assert signals.height > snapshots.height
    assert set(signals.get_column("name").unique()) == {
        "mid",
        "micro_price",
        "quoted_spread_bps",
        "imbalance",
    }
    # Every derived row is joinable back to the book state that produced it.
    assert set(signals.get_column("book_seq")) <= set(snapshots.get_column("book_seq"))


@pytest.mark.core
def test_derived_pairs_share_a_book_seq() -> None:
    """The two tables are written in one pass; a mismatch would break every join."""
    pairs = list(iter_derived_rows(FIXTURE, limit=50))
    for snap, sigs in pairs:
        assert all(row["book_seq"] == snap["book_seq"] for row in sigs)


@pytest.mark.core
def test_a_capture_with_no_book_frames_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    path = tmp_path / "quiet.jsonl"
    path.write_text(
        '{"v":1,"kind":"msg","seq":0,"recv_ns":1,"recv_wall_ns":2,'
        '"payload":"{\\"channel\\":\\"heartbeat\\"}"}\n'
    )
    assert main(["signals", str(path), "--out", str(tmp_path / "pq")]) == 1
    assert "no book frames" in capsys.readouterr().err


def test_polars_reads_the_list_columns_back(tmp_path: Path) -> None:
    assert main(["signals", str(FIXTURE), "--out", str(tmp_path), "--limit", "50"]) == 0
    frame = read_table(tmp_path, "snapshot")
    first = frame.select(pl.col("bid_px").list.len()).to_series().max()
    assert first == 10  # --levels default
