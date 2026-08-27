"""Latency measurement: the percentile maths, and the harness around the book."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from l2tca.bench.harness import format_report, run_book_benchmark
from l2tca.bench.latency import LatencyRecorder, percentile_ns, timed
from l2tca.book.base import BookView
from l2tca.feed.messages import BookSnapshot, BookUpdate


def test_percentiles_use_nearest_rank_so_they_are_observed_values() -> None:
    samples = list(range(1, 101))
    assert percentile_ns(samples, 50) == 50
    assert percentile_ns(samples, 99) == 99
    assert percentile_ns(samples, 100) == 100
    assert percentile_ns(samples, 1) == 1
    # No interpolation: every reported value actually occurred.
    assert percentile_ns([10, 20], 75) == 20


def test_percentile_edge_cases() -> None:
    assert math.isnan(percentile_ns([], 50))
    assert percentile_ns([42], 99.9) == 42
    with pytest.raises(ValueError):
        percentile_ns([1], 0)
    with pytest.raises(ValueError):
        percentile_ns([1], 101)


def test_recorder_summarises_samples() -> None:
    recorder = LatencyRecorder("stage")
    for value in [10, 20, 30, 40, 1000]:
        recorder.record(value)

    stats = recorder.stats()
    assert stats.count == 5
    assert stats.min_ns == 10
    assert stats.max_ns == 1000
    assert stats.mean_ns == 220
    assert stats.p50_ns == 30
    # The tail is why every sample is kept: the mean hides the 1000.
    assert stats.percentiles[99.0] == 1000
    assert stats.throughput_per_s == pytest.approx(5 / (1100 / 1e9))


def test_empty_recorder_reports_nan_not_zero() -> None:
    stats = LatencyRecorder("empty").stats()
    assert stats.count == 0
    assert math.isnan(stats.p50_ns)
    assert math.isnan(stats.mean_ns)


def test_measure_times_calls_and_counts_but_does_not_time_failures() -> None:
    recorder = LatencyRecorder("stage")
    with recorder.measure():
        sum(range(1000))
    with pytest.raises(RuntimeError), recorder.measure():
        raise RuntimeError("boom")

    assert len(recorder) == 1, "a failed call has no meaningful duration"
    assert recorder.errors == 1


def test_timed_reports_elapsed_and_restores_the_collector() -> None:
    import gc

    was_enabled = gc.isenabled()
    recorder = LatencyRecorder("region")
    with timed(recorder) as elapsed:
        sum(range(10_000))
    assert elapsed[0] > 0
    assert len(recorder) == 1
    assert gc.isenabled() == was_enabled


def test_stats_serialise_for_a_report() -> None:
    recorder = LatencyRecorder("stage")
    recorder.record(5)
    payload = recorder.stats().to_dict()
    assert payload["name"] == "stage"
    assert payload["percentiles"]["p50"] == 5


# -- the harness -----------------------------------------------------------


class CountingBook:
    """A book stand-in with known costs, so the harness itself can be tested."""

    def __init__(self) -> None:
        self.snapshots = 0
        self.updates = 0
        self.views = 0

    def apply_snapshot(self, _frame: BookSnapshot) -> None:
        self.snapshots += 1

    def apply_update(self, _frame: BookUpdate) -> None:
        self.updates += 1

    def view(self, n: int = 10) -> BookView:
        self.views += 1
        return BookView("BTC/USD", self.views, 0, 0, None, (), ())


def test_harness_times_every_stage(capture: Path) -> None:
    book = CountingBook()
    report = run_book_benchmark(capture, book_factory=lambda: book, warmup=0)

    assert report.book_frames == book.snapshots + book.updates
    assert book.views == report.book_frames
    assert report.messages > report.book_frames  # heartbeats and acks are parsed too

    for name in ("parse", "book.apply_update", "book.view(n=10)"):
        stage = report.stage(name)
        assert stage is not None and stage.count > 0, name
        assert stage.p50_ns > 0


def test_warmup_samples_are_excluded(capture: Path) -> None:
    full = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    trimmed = run_book_benchmark(capture, book_factory=CountingBook, warmup=20)
    assert trimmed.stage("book.view(n=10)").count == full.stage("book.view(n=10)").count - 20
    assert trimmed.warmup == 20


def test_limit_bounds_the_run(capture: Path) -> None:
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0, limit=25)
    assert report.book_frames == 25


def test_harness_runs_against_the_unimplemented_book(capture: Path) -> None:
    """The whole point: measurement scaffolding exists before the algorithm does."""
    report = run_book_benchmark(capture, warmup=0)

    assert report.stage("parse").count > 0
    for name in ("book.apply_snapshot", "book.apply_update", "book.view(n=10)"):
        stage = report.stage(name)
        assert stage.note == "not implemented"
        assert stage.count == 0
    assert "not implemented" in format_report(report)


def test_report_serialises_with_its_environment(capture: Path) -> None:
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    payload = report.to_dict()
    # Latency numbers without the machine that produced them are not comparable.
    assert payload["environment"]["python"]
    assert payload["environment"]["platform"]
    assert payload["messages_per_s"] > 0
    assert len(payload["stages"]) == 4
    assert "p99.9" in payload["stages"][0]["percentiles"]


def test_format_report_is_a_readable_table(capture: Path) -> None:
    text = format_report(run_book_benchmark(capture, book_factory=CountingBook, warmup=0))
    assert "latency per call, microseconds" in text
    assert "p99.9" in text
    assert "book.apply_update" in text
