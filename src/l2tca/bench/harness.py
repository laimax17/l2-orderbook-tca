"""Benchmark harness: replay a capture through the book and time each stage.

The headline metric is **recv -> book-updated**: from the instant a frame is
stamped on receipt to the instant the book has finished applying it. That is the
number that decides whether a strategy reads a stale book, and it is the only
one that covers the whole path rather than one function.

What "recv" means depends on the source, and the distinction matters:

* **live** -- stamped immediately after the socket read, so the measurement
  covers decode, apply and view, but not network or kernel time.
* **replay** -- stamped when the replayer hands the frame over
  (``restamp=True``). Network time is not in the recording and cannot be
  recovered from it, so a replayed end-to-end number is a pipeline number.
  Comparing it against a live one is comparing two different things.

Three properties make the numbers trustworthy:

* **Replay, not live.** A fixed file means two runs measure the same work in the
  same order. A live feed cannot be A/B tested against itself.
* **Unpaced.** No sleeps, so the measurement is of the code and not of the
  exchange's message rate.
* **Warmup and snapshot rebuilds excluded.** The first frames pay for CPython's
  inline caches and the book's initial allocations, and a snapshot rebuild is a
  different operation with a different cost -- both belong in their own bucket,
  not in the update distribution.

The harness runs today against the unimplemented book: it reports the parse
stage normally and marks the book stages as not implemented. The measurement
scaffolding exists before the algorithm so an implementation choice can be
evaluated the moment it exists.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from l2tca.bench.latency import DEFAULT_PERCENTILES, LatencyRecorder
from l2tca.bench.report import BenchReport, environment
from l2tca.book.order_book import OrderBook
from l2tca.feed.messages import BookSnapshot, BookUpdate
from l2tca.feed.parser import parse
from l2tca.feed.replay import iter_raw_messages

__all__ = ["END_TO_END", "run_book_benchmark"]

#: The end-to-end stage, named once so the report and the tests agree.
END_TO_END = "recv -> book-updated"


def _call(recorder: LatencyRecorder, enabled: bool, counting: bool, fn, *args) -> tuple[bool, bool]:
    """Run one stage. Returns ``(still_enabled, succeeded)``.

    A stage whose first call raises :class:`NotImplementedError` is disabled for
    the rest of the run, so the harness stays useful against a partial book.
    """
    if not enabled:
        return False, False
    start = time.perf_counter_ns()
    try:
        fn(*args)
    except NotImplementedError:
        return False, False
    except Exception:
        recorder.errors += 1
        return True, False
    if counting:
        recorder.record(time.perf_counter_ns() - start)
    return True, True


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
    """Replay ``path`` through a book, timing the end-to-end path and each stage.

    Args:
        book_factory: Builds the object under test. Defaults to
            :class:`~l2tca.book.order_book.OrderBook`. Pass an alternative to
            compare two internal representations against the same capture --
            which is the reason this argument exists.
        limit: Stop after this many update frames. ``None`` runs the whole file.
        warmup: Update frames applied before sampling begins.
        view_levels: Depth requested from ``book.view()``; its cost scales with it.
        freeze_gc: Hold the cyclic collector for the measured loop.
    """
    source = str(path)
    book = (book_factory or (lambda: OrderBook(symbol, depth)))()

    e2e = LatencyRecorder(END_TO_END)
    parse_rec = LatencyRecorder("parse")
    update_rec = LatencyRecorder("book.apply_update")
    snapshot_rec = LatencyRecorder("book.apply_snapshot")
    view_rec = LatencyRecorder(f"book.view(n={view_levels})")

    update_on = snapshot_on = view_on = True
    messages = updates = snapshots = 0

    was_enabled = gc.isenabled()
    if freeze_gc and was_enabled:
        gc.disable()
    run_start = time.perf_counter_ns()
    try:
        for message in iter_raw_messages(source):
            messages += 1

            # Re-stamped here rather than taken from the recording: the file's
            # recv_ns comes from another process's monotonic clock and has no
            # meaning against this one. See the module docstring.
            recv_ns = time.perf_counter_ns()

            start = time.perf_counter_ns()
            parsed = parse(message.payload)
            parse_ns = time.perf_counter_ns() - start

            is_update = isinstance(parsed, BookUpdate)
            is_snapshot = isinstance(parsed, BookSnapshot)
            if not (is_update or is_snapshot):
                continue

            counting = is_update and updates >= warmup
            if counting:
                parse_rec.record(parse_ns)

            if is_snapshot:
                snapshots += 1
                # Timed into its own bucket and never counted toward the update
                # distribution: a rebuild is a different operation.
                snapshot_on, _ = _call(snapshot_rec, snapshot_on, True, book.apply_snapshot, parsed)
                _call(view_rec, view_on, False, book.view, view_levels)
                continue

            updates += 1
            update_on, applied = _call(update_rec, update_on, counting, book.apply_update, parsed)
            view_on, viewed = _call(view_rec, view_on, counting, book.view, view_levels)
            if counting and applied and viewed:
                e2e.record(time.perf_counter_ns() - recv_ns)

            if limit is not None and updates >= limit:
                break
    finally:
        elapsed_ns = time.perf_counter_ns() - run_start
        if freeze_gc and was_enabled:
            gc.enable()

    for recorder, on in ((update_rec, update_on), (snapshot_rec, snapshot_on), (view_rec, view_on)):
        if not on:
            recorder.note = "not implemented"
    if not (update_on and view_on):
        e2e.note = "not implemented"

    return BenchReport(
        source=source,
        messages=messages,
        update_frames=updates,
        snapshot_frames=snapshots,
        warmup=min(warmup, updates),
        elapsed_ns=elapsed_ns,
        stages=[
            e2e.stats(percentiles),
            parse_rec.stats(percentiles),
            update_rec.stats(percentiles),
            view_rec.stats(percentiles),
            snapshot_rec.stats(percentiles),
        ],
        environment=environment(),
    )
