"""The harness around the book: end-to-end timing, warmup and snapshot exclusion."""

from __future__ import annotations

from pathlib import Path

from l2tca.bench.harness import END_TO_END, run_book_benchmark
from l2tca.bench.report import format_report, render_histogram
from l2tca.book.types import BookView
from l2tca.feed.messages import BookSnapshot, BookUpdate


class CountingBook:
    """A book stand-in with known behaviour, so the harness itself can be tested."""

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


class UnimplementedBook:
    """A book whose every stage is still a stub.

    The harness has to survive this: measurement scaffolding is only useful if
    it exists *before* the algorithm does. Injected explicitly rather than
    relying on the real book being unwritten, so these tests keep testing the
    harness once the real book works.
    """

    def apply_snapshot(self, _frame: BookSnapshot) -> None:
        raise NotImplementedError("core logic: implement by hand")

    def apply_update(self, _frame: BookUpdate) -> None:
        raise NotImplementedError("core logic: implement by hand")

    def view(self, n: int = 10) -> BookView:
        raise NotImplementedError("core logic: implement by hand")


def test_harness_times_the_end_to_end_path_and_each_stage(capture: Path) -> None:
    book = CountingBook()
    report = run_book_benchmark(capture, book_factory=lambda: book, warmup=0)

    assert report.update_frames == book.updates
    assert report.snapshot_frames == book.snapshots >= 1
    assert report.messages > report.update_frames  # heartbeats and acks are parsed too

    e2e = report.stage(END_TO_END)
    assert e2e is not None and e2e.count == report.update_frames
    assert e2e.p50_ns > 0

    for name in ("parse", "book.apply_update", "book.view(n=10)"):
        stage = report.stage(name)
        assert stage is not None and stage.count > 0, name


def test_end_to_end_is_at_least_the_sum_of_its_parts(capture: Path) -> None:
    """recv -> book-updated spans parse, apply and view, so it cannot be faster."""
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    e2e = report.stage(END_TO_END)
    apply_stage = report.stage("book.apply_update")
    assert e2e.total_ns >= apply_stage.total_ns


def test_snapshot_rebuilds_are_excluded_from_the_update_distribution(capture: Path) -> None:
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    assert report.snapshot_frames >= 1
    assert report.stage("book.apply_snapshot").count == report.snapshot_frames
    assert report.stage("book.apply_update").count == report.update_frames
    assert report.stage(END_TO_END).count == report.update_frames


def test_warmup_samples_are_excluded(capture: Path) -> None:
    full = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    trimmed = run_book_benchmark(capture, book_factory=CountingBook, warmup=20)
    assert trimmed.stage(END_TO_END).count == full.stage(END_TO_END).count - 20
    assert trimmed.warmup == 20


def test_limit_bounds_the_run(capture: Path) -> None:
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0, limit=25)
    assert report.update_frames == 25


def test_harness_runs_against_an_unimplemented_book(capture: Path) -> None:
    """The point: measurement scaffolding exists before the algorithm does."""
    report = run_book_benchmark(capture, book_factory=UnimplementedBook, warmup=0)

    assert report.stage("parse").count > 0
    for name in (END_TO_END, "book.apply_snapshot", "book.apply_update", "book.view(n=10)"):
        stage = report.stage(name)
        assert stage.note == "not implemented"
        assert stage.count == 0
    assert "not implemented" in format_report(report)


def test_harness_measures_the_real_book(capture: Path) -> None:
    """The default factory builds the real book, and every stage yields samples."""
    report = run_book_benchmark(capture, warmup=0)

    assert report.update_frames > 0
    for name in (END_TO_END, "parse", "book.apply_update", "book.view(n=10)"):
        stage = report.stage(name)
        assert stage.note == "", f"{name}: {stage.note}"
        assert stage.count > 0 and stage.p50_ns > 0, name
    assert "not implemented" not in format_report(report)


def test_report_serialises_with_its_environment(capture: Path) -> None:
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    payload = report.to_dict()
    # Latency numbers without the machine that produced them are not comparable.
    assert payload["environment"]["python"]
    assert payload["environment"]["platform"]
    assert payload["messages_per_s"] > 0
    assert len(payload["stages"]) == 5
    assert "p99.9" in payload["stages"][0]["percentiles"]


def test_format_report_renders_a_table_and_optional_histograms(capture: Path) -> None:
    report = run_book_benchmark(capture, book_factory=CountingBook, warmup=0)
    plain = format_report(report)
    assert "latency per call, microseconds" in plain
    assert "p99.9" in plain
    assert END_TO_END in plain

    with_hist = format_report(report, histograms=True)
    assert len(with_hist) > len(plain)
    assert "#" in with_hist


def test_render_histogram_handles_an_empty_stage() -> None:
    from l2tca.bench.histogram import Histogram

    assert "no samples" in render_histogram(Histogram((), (), True))
