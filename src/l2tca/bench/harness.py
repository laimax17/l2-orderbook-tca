"""Benchmark harness: replay a capture through the book and time each stage.

Wraps the calls that matter -- parse, ``apply_*``, ``view`` -- and reports the
distribution of each. Three properties make the numbers trustworthy:

* **Replay, not live.** The input is a fixed file, so two runs measure the same
  work in the same order. A live feed cannot be A/B tested against itself.
* **Unpaced.** ``speed=0`` removes sleeps, so the measurement is of the code,
  not of the exchange's message rate.
* **Warmup excluded.** The first few hundred updates pay for CPython's inline
  caches and the book's initial allocations; including them puts a fixed
  artefact in every percentile.

The harness runs today, against the unimplemented book: it reports the parse
stage normally and marks the book stages as not implemented. That is the point
-- the measurement scaffolding is in place before the first line of the
algorithm, so an implementation choice can be evaluated the moment it exists.
"""

from __future__ import annotations

import gc
import json
import math
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from l2tca.bench.latency import DEFAULT_PERCENTILES, LatencyRecorder, LatencyStats
from l2tca.book.l2_book import L2Book
from l2tca.feed.messages import BookSnapshot, BookUpdate, parse
from l2tca.feed.replay import iter_raw_messages

__all__ = ["BenchReport", "StageReport", "format_report", "run_book_benchmark"]

StageReport = LatencyStats


@dataclass(slots=True)
class BenchReport:
    """Everything one benchmark run produced."""

    source: str
    messages: int
    book_frames: int
    warmup: int
    elapsed_ns: int
    stages: list[LatencyStats] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)

    @property
    def messages_per_s(self) -> float:
        """End-to-end replay throughput, including file IO and JSON decoding."""
        if self.elapsed_ns <= 0:
            return math.nan
        return self.messages / (self.elapsed_ns / 1e9)

    def stage(self, name: str) -> LatencyStats | None:
        return next((s for s in self.stages if s.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "messages": self.messages,
            "book_frames": self.book_frames,
            "warmup": self.warmup,
            "elapsed_ns": self.elapsed_ns,
            "messages_per_s": self.messages_per_s,
            "environment": self.environment,
            "stages": [s.to_dict() for s in self.stages],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _environment() -> dict[str, Any]:
    """Recorded with every run: latency numbers are meaningless without it."""
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
    }


def run_book_benchmark(
    path: Path | str,
    *,
    book_factory: Callable[[], Any] | None = None,
    symbol: str = "BTC/USD",
    depth: int = 100,
    limit: int | None = None,
    warmup: int = 500,
    view_levels: int = 10,
    percentiles: tuple[float, ...] = DEFAULT_PERCENTILES,
    freeze_gc: bool = True,
) -> BenchReport:
    """Replay ``path`` through a book, timing parse, apply and view.

    Args:
        book_factory: Builds the object under test. Defaults to
            :class:`~l2tca.book.l2_book.L2Book`. Pass an alternative to compare
            two internal representations against the same capture -- which is
            the reason this argument exists.
        limit: Stop after this many book frames. ``None`` runs the whole file.
        warmup: Book frames applied before sampling begins.
        view_levels: Depth requested from ``book.view()``, since the cost of
            producing a view scales with it.
        freeze_gc: Hold the cyclic collector for the measured loop. See the
            module docstring in :mod:`l2tca.bench.latency`.

    A stage whose first call raises :class:`NotImplementedError` is disabled for
    the rest of the run and reported with a note, so the harness is useful
    against a partially implemented book.
    """
    source = str(path)
    book = (book_factory or (lambda: L2Book(symbol, depth)))()

    parse_rec = LatencyRecorder("parse")
    apply_rec = LatencyRecorder("book.apply_update")
    snapshot_rec = LatencyRecorder("book.apply_snapshot")
    view_rec = LatencyRecorder(f"book.view(n={view_levels})")

    apply_enabled = snapshot_enabled = view_enabled = True
    messages = 0
    book_frames = 0
    sampled = 0

    was_enabled = gc.isenabled()
    if freeze_gc and was_enabled:
        gc.disable()
    run_start = time.perf_counter_ns()
    try:
        for message in iter_raw_messages(source):
            messages += 1

            start = time.perf_counter_ns()
            parsed = parse(message.payload)
            elapsed = time.perf_counter_ns() - start
            counting = book_frames >= warmup
            if counting:
                parse_rec.record(elapsed)

            if not isinstance(parsed, BookSnapshot | BookUpdate):
                continue
            book_frames += 1

            if isinstance(parsed, BookSnapshot):
                snapshot_enabled = _run_stage(
                    snapshot_rec, snapshot_enabled, counting, book.apply_snapshot, parsed
                )
            else:
                apply_enabled = _run_stage(
                    apply_rec, apply_enabled, counting, book.apply_update, parsed
                )

            view_enabled = _run_stage(view_rec, view_enabled, counting, book.view, view_levels)

            if counting:
                sampled += 1
            if limit is not None and book_frames >= limit:
                break
    finally:
        elapsed_ns = time.perf_counter_ns() - run_start
        if freeze_gc and was_enabled:
            gc.enable()

    for recorder, enabled in (
        (apply_rec, apply_enabled),
        (snapshot_rec, snapshot_enabled),
        (view_rec, view_enabled),
    ):
        if not enabled:
            recorder.note = "not implemented"

    return BenchReport(
        source=source,
        messages=messages,
        book_frames=book_frames,
        warmup=min(warmup, book_frames),
        elapsed_ns=elapsed_ns,
        stages=[
            parse_rec.stats(percentiles),
            snapshot_rec.stats(percentiles),
            apply_rec.stats(percentiles),
            view_rec.stats(percentiles),
        ],
        environment=_environment(),
    )


def _run_stage(
    recorder: LatencyRecorder,
    enabled: bool,
    counting: bool,
    fn: Callable[..., Any],
    *args: Any,
) -> bool:
    """Call ``fn``, timing it when past warmup. Returns whether it stays enabled."""
    if not enabled:
        return False
    start = time.perf_counter_ns()
    try:
        fn(*args)
    except NotImplementedError:
        return False
    except Exception:
        recorder.errors += 1
        return True
    if counting:
        recorder.record(time.perf_counter_ns() - start)
    return True


def format_report(report: BenchReport) -> str:
    """Fixed-width table, in microseconds, for the terminal."""
    header = (
        f"{'stage':<26}{'n':>9}{'p50':>10}{'p90':>10}{'p99':>10}"
        f"{'p99.9':>10}{'max':>10}{'err':>6}"
    )
    lines = [
        f"source        : {report.source}",
        f"messages      : {report.messages}  (book frames: {report.book_frames}, "
        f"warmup: {report.warmup})",
        f"replay wall   : {report.elapsed_ns / 1e9:.3f}s  "
        f"({report.messages_per_s:,.0f} msg/s end to end)",
        f"environment   : {report.environment.get('implementation')} "
        f"{report.environment.get('python')} on {report.environment.get('platform')}",
        "",
        "latency per call, microseconds",
        header,
        "-" * len(header),
    ]
    for stage in report.stages:
        if stage.note:
            lines.append(f"{stage.name:<26}{'-':>9}   {stage.note}")
            continue
        p = stage.percentiles
        lines.append(
            f"{stage.name:<26}{stage.count:>9}"
            f"{_us(p.get(50.0)):>10}{_us(p.get(90.0)):>10}{_us(p.get(99.0)):>10}"
            f"{_us(p.get(99.9)):>10}{_us(stage.max_ns):>10}{stage.errors:>6}"
        )
    return "\n".join(lines)


def _us(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value / 1000:.3f}"
