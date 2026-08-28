"""Sample collection, percentiles and histograms for nanosecond latencies.

Measurement discipline
----------------------
*Keep every sample.* Raw ``int`` nanoseconds in a flat list, not a running mean
and variance. Book updates are not normally distributed -- the interesting
number is the p99, and no summary computed from a mean recovers it. A ten-minute
capture is a few hundred thousand samples and a few megabytes; that is a cheap
price for an exact tail.

*Report the tail.* A mean hides exactly the behaviour that matters: the update
that walks the whole book, or the allocation that triggers a collection. p99 is
what decides whether a strategy acts on a stale book.

*Percentiles by nearest rank.* No interpolation, so a reported p99 is a latency
that actually occurred. Interpolated percentiles invent values that were never
observed, which is a strange thing to put in a latency report.

*Do not measure the collector by accident.* :func:`timed` and the harness hold
the cyclic collector around a measured region. GC pauses are real and worth
measuring, but deliberately -- comparing two runs -- rather than landing at
random in one run's tail and making it irreproducible.
"""

from __future__ import annotations

import gc
import math
import statistics
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from l2tca.bench.histogram import Histogram, histogram

__all__ = [
    "DEFAULT_PERCENTILES",
    "LatencyRecorder",
    "LatencyStats",
    "percentile_ns",
    "timed",
]

#: Reported by default. The spec asks for p50/p90/p99/max; p99.9 is included
#: because at a few hundred updates a second it is still several events a
#: minute -- a recurring event, not a curiosity.
DEFAULT_PERCENTILES = (50.0, 90.0, 99.0, 99.9)


def percentile_ns(samples: list[int], pct: float) -> float:
    """Nearest-rank percentile over ``samples``, which must be sorted."""
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
    hist: Histogram
    errors: int = 0
    note: str = ""

    @property
    def p50_ns(self) -> float:
        return self.percentiles.get(50.0, math.nan)

    @property
    def p99_ns(self) -> float:
        return self.percentiles.get(99.0, math.nan)

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
            "percentiles": {f"p{k:g}": v for k, v in self.percentiles.items()},
            "histogram": self.hist.to_dict(),
            "note": self.note,
        }


class LatencyRecorder:
    """Collects per-call durations for one stage."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.errors = 0
        self.note = ""
        self._samples: list[int] = []

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def samples(self) -> list[int]:
        """The raw samples in call order. Returned as-is; do not mutate."""
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
            return LatencyStats(
                name=self.name,
                count=0,
                total_ns=0,
                min_ns=math.nan,
                mean_ns=math.nan,
                stdev_ns=math.nan,
                max_ns=math.nan,
                percentiles=dict.fromkeys(percentiles, math.nan),
                hist=Histogram((), (), True),
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
            hist=histogram(ordered),
            errors=self.errors,
            note=self.note,
        )


@contextmanager
def timed(
    recorder: LatencyRecorder | None = None, *, freeze_gc: bool = True
) -> Iterator[list[int]]:
    """Measure a region, optionally holding the cyclic collector.

    Yields a one-element list that receives the elapsed nanoseconds, so the
    duration is available even when no recorder was supplied.
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
