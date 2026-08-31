"""Plotting: derivations, error paths and that a figure actually renders."""

from __future__ import annotations

from pathlib import Path

import pytest

from l2tca.bench.harness import run_book_benchmark
from l2tca.io.writer import PartitionedParquetWriter
from l2tca.plot.depth import plot_depth_snapshot
from l2tca.plot.latency import plot_latency_histogram
from l2tca.plot.spread import plot_spread_series, spread_frame

BASE_NS = 1_767_346_200_000_000_000

matplotlib = pytest.importorskip("matplotlib", reason="plotting extra not installed")


def _snapshot_row(seq: int, wall_ns: int, bid: float, ask: float) -> dict:
    return {
        "book_seq": seq,
        "recv_ns": seq,
        "recv_wall_ns": wall_ns,
        "exchange_ts_ns": None,
        "checksum_ok": True,
        "bid_px": [bid, bid - 0.1, bid - 0.2],
        "bid_qty": [1.0, 2.0, 3.0],
        "ask_px": [ask, ask + 0.1, ask + 0.2],
        "ask_qty": [1.5, 2.5, 3.5],
    }


@pytest.fixture
def snapshot_root(tmp_path: Path) -> Path:
    with PartitionedParquetWriter(tmp_path, "snapshot", "BTC-USD") as writer:
        writer.write_rows(
            [
                _snapshot_row(i, BASE_NS + i * 1_000_000_000, 100.0 - i * 0.01, 100.5 + i * 0.01)
                for i in range(20)
            ]
        )
    return tmp_path


def test_spread_frame_derives_touch_spread_and_bps(snapshot_root: Path) -> None:
    frame = spread_frame(snapshot_root)
    assert frame.height == 20
    first = frame.row(0, named=True)
    assert first["best_bid"] == pytest.approx(100.0)
    assert first["best_ask"] == pytest.approx(100.5)
    assert first["spread"] == pytest.approx(0.5)
    assert first["spread_bps"] == pytest.approx(1e4 * 0.5 / 100.25)


def test_spread_frame_drops_one_sided_views(tmp_path: Path) -> None:
    """A spread is not defined for them; a null would only reappear as a gap."""
    row = _snapshot_row(1, BASE_NS, 100.0, 100.5)
    row["ask_px"], row["ask_qty"] = [], []
    with PartitionedParquetWriter(tmp_path, "snapshot", "BTC-USD") as writer:
        writer.write_row(row)
        writer.write_row(_snapshot_row(2, BASE_NS + 1, 100.0, 100.5))
    assert spread_frame(tmp_path).height == 1


def test_spread_plot_renders(snapshot_root: Path, tmp_path: Path) -> None:
    fig = plot_spread_series(snapshot_root)
    out = tmp_path / "spread.png"
    fig.savefig(out)
    assert out.stat().st_size > 0


def test_depth_plot_renders_the_last_view_by_default(snapshot_root: Path, tmp_path: Path) -> None:
    fig = plot_depth_snapshot(snapshot_root)
    assert "book_seq=19" in fig.axes[0].get_title()
    out = tmp_path / "depth.png"
    fig.savefig(out)
    assert out.stat().st_size > 0


def test_depth_plot_accepts_an_explicit_seq(snapshot_root: Path) -> None:
    fig = plot_depth_snapshot(snapshot_root, book_seq=3)
    assert "book_seq=3" in fig.axes[0].get_title()


def test_depth_plot_rejects_a_missing_seq(snapshot_root: Path) -> None:
    with pytest.raises(ValueError, match="book_seq=999"):
        plot_depth_snapshot(snapshot_root, book_seq=999)


def test_latency_plot_renders_from_a_bench_report(capture: Path, tmp_path: Path) -> None:
    from tests.bench.test_harness import CountingBook

    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    fig = plot_latency_histogram(report.to_dict())
    out = tmp_path / "latency.png"
    fig.savefig(out)
    assert out.stat().st_size > 0


def test_latency_plot_reads_a_json_file(capture: Path, tmp_path: Path) -> None:
    from tests.bench.test_harness import CountingBook

    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    path = tmp_path / "bench.json"
    path.write_text(report.to_json())
    assert plot_latency_histogram(path, stage="parse") is not None


def test_latency_plot_is_explicit_when_the_book_is_unimplemented(capture: Path) -> None:
    from tests.bench.test_harness import UnimplementedBook

    report = run_book_benchmark(capture, book_factory=UnimplementedBook, warmup=0)
    fig = plot_latency_histogram(report.to_dict())
    # parse is the only stage with samples when the book stages are stubs.
    assert "parse" in fig.axes[0].get_title()


def test_latency_plot_rejects_an_unknown_stage(capture: Path) -> None:
    from tests.bench.test_harness import CountingBook

    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    with pytest.raises(ValueError, match="no samples for stage"):
        plot_latency_histogram(report.to_dict(), stage="nope")


def test_missing_snapshot_table_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no snapshot table"):
        plot_spread_series(tmp_path)
