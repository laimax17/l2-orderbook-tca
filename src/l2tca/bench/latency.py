"""Sample collection and percentile reporting for nanosecond latencies.

Measurement discipline
----------------------
*Keep every sample.* The recorder stores raw ``int`` nanoseconds in a flat list
rather than accumulating a running mean and variance. Book updates are not
normally distributed -- the interesting number is the p99.9, and no summary
statistic computed from a mean can recover it. A ten-minute capture is a few
hundred thousand samples, which is a few megabytes; that is a cheap price for
an exact tail.

*Report the tail, not the mean.* A mean update latency hides exactly the
behaviour that matters: the rare update that walks the whole book, or the
allocation that triggers a GC pause. ``p99`` and ``p99.9`` are the numbers that
decide whether a strategy acts on a stale book.

*Do not measure the garbage collector by accident.* :func:`timed` and the
harness disable the cyclic collector around a measured region. GC pauses are
real and worth measuring, but they should be measured deliberately (compare two
runs) rather than landing at random in one run's tail and making it
irreproducible.

*Percentiles by nearest rank.* No interpolation: ``p99`` of the samples is a
value that actually occurred. Interpolated percentiles invent latencies that
were never observed, which is a strange thing to put in a latency report.
"""

from __future__ import annotations

import gc
import math
import statistics
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

__all__ = ["LatencyRecorder", "LatencyStats", "percentile_ns", "timed"]

#: Percentiles reported by default. p99.9 is included because at a few hundred
#: updates per second it is still several events per minute -- a real, recurring
#: event rather than a curiosity.
DEFAULT_PERCENTILES = (50.0, 90.0, 99.0, 99.9)


def percentile_ns(samples: list[int], pct: float) -> float:
    """Nearest-rank percentile over ``samples``. ``samples`` must be sorted."""
    if not samples:
        return math.nan
    if not 0 < pct <= 100:
        raise ValueError("percentile must be in (0, 100]")
    rank = math.ceil(pct / 100 * len(samples))
    return float(samples[min(rank, len(samples)) - 1])


@dataclass(frozen=True, slots=True)
class LatencyStats:
    """Summary of one measured stage. All latencies in nanoseconds."""

    name: str
    count: int
    total_ns: int
    min_ns: float
    mean_ns: float
    stdev_ns: float
    max_ns: float
    percentiles: dict[float, float]
    errors: int = 0
    note: str = ""

    @property
    def p50_ns(self) -> float:
        return self.percentiles.get(50.0, math.nan)

    @property
    def p99_ns(self) -> float:
        return self.percentiles.get(99.0, math.nan)

    @property
    def throughput_per_s(self) -> float:
        """Calls per second, measured over summed in-call time only.

        This is the isolated cost of the stage, not the rate the feed can be
        consumed end to end -- for that, compare against wall-clock elapsed in
        :class:`~l2tca.bench.harness.BenchReport`.
        """
        if self.total_ns <= 0:
            return math.nan
        return self.count / (self.total_ns / 1e9)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "count": self.count,
            "errors": self.errors,
            "total_ns": self.total_ns,
            "min_ns": self.min_ns,
            "mean_ns": self.mean_ns,
            "stdev_ns": self.stdev_ns,
            "max_ns": self.max_ns,
            "throughput_per_s": self.throughput_per_s,
            "percentiles": {f"p{k:g}": v for k, v in self.percentiles.items()},
            "note": self.note,
        }


class LatencyRecorder:
    """Collects per-call durations for one stage."""

    def __init__(self, name: str, *, capacity_hint: int = 0) -> None:
        self.name = name
        self.errors = 0
        self.note = ""
        self._samples: list[int] = []
        if capacity_hint > 0:
            # Reserve up front so list growth does not land in the middle of a
            # measured region and show up as a latency spike of its own.
            self._samples = [0] * capacity_hint
            self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def samples(self) -> list[int]:
        """The raw samples, in call order. Returned as-is; do not mutate."""
        return self._samples

    def record(self, duration_ns: int) -> None:
        self._samples.append(duration_ns)

    @contextmanager
    def measure(self) -> Iterator[None]:
        """Time one call. Exceptions are counted and re-raised, never timed."""
        start = time.perf_counter_ns()
        try:
            yield
        except Exception:
            self.errors += 1
            raise
        else:
            self.record(time.perf_counter_ns() - start)

    def stats(self, percentiles: tuple[float, ...] = DEFAULT_PERCENTILES) -> LatencyStats:
        ordered = sorted(self._samples)
        count = len(ordered)
        if count == 0:
            nan = math.nan
            return LatencyStats(
                name=self.name,
                count=0,
                total_ns=0,
                min_ns=nan,
                mean_ns=nan,
                stdev_ns=nan,
                max_ns=nan,
                percentiles=dict.fromkeys(percentiles, nan),
                errors=self.errors,
                note=self.note,
            )
        return LatencyStats(
            name=self.name,
            count=count,
            total_ns=sum(ordered),
            min_ns=float(ordered[0]),
            mean_ns=statistics.fmean(ordered),
            stdev_ns=statistics.stdev(ordered) if count > 1 else 0.0,
            max_ns=float(ordered[-1]),
            percentiles={p: percentile_ns(ordered, p) for p in percentiles},
            errors=self.errors,
            note=self.note,
        )


@contextmanager
def timed(
    recorder: LatencyRecorder | None = None, *, freeze_gc: bool = True
) -> Iterator[list[int]]:
    """Measure a region, optionally holding the cyclic collector.

    Yields a one-element list that receives the elapsed nanoseconds, so the
    duration is available to the caller even when no recorder was supplied.
    """
    out: list[int] = []
    was_enabled = gc.isenabled()
    if freeze_gc and was_enabled:
        gc.disable()
    start = time.perf_counter_ns()
    try:
        yield out
    finally:
        elapsed = time.perf_counter_ns() - start
        if freeze_gc and was_enabled:
            gc.enable()
        out.append(elapsed)
        if recorder is not None:
            recorder.record(elapsed)
