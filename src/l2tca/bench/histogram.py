"""Bucketing latency samples for display.

Log-spaced buckets by default. Latency distributions run over two or three
orders of magnitude with a long right tail; linear buckets put almost all the
mass in the first bar and render the tail as a flat line -- which is the part
worth seeing.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

__all__ = ["Histogram", "histogram"]


@dataclass(frozen=True, slots=True)
class Histogram:
    """Counts per bucket, with the bucket edges that produced them."""

    edges_ns: tuple[float, ...]
    counts: tuple[int, ...]
    log_scale: bool

    @property
    def total(self) -> int:
        return sum(self.counts)

    def to_dict(self) -> dict:
        return {
            "edges_ns": list(self.edges_ns),
            "counts": list(self.counts),
            "log_scale": self.log_scale,
        }


def histogram(samples: list[int], *, buckets: int = 24, log_scale: bool = True) -> Histogram:
    """Bucket ``samples`` for display.

    See the module docstring for why the default is log-spaced.
    """
    if not samples:
        return Histogram((), (), log_scale)

    lo, hi = float(min(samples)), float(max(samples))
    if log_scale:
        lo = max(lo, 1.0)
        hi = max(hi, lo * 1.000001)
        step = (math.log10(hi) - math.log10(lo)) / buckets
        edges = tuple(10 ** (math.log10(lo) + i * step) for i in range(buckets + 1))
    else:
        hi = max(hi, lo + 1.0)
        step = (hi - lo) / buckets
        edges = tuple(lo + i * step for i in range(buckets + 1))

    counts = [0] * buckets
    for value in samples:
        # Linear scan of edges would be O(n*buckets); bisect keeps it O(n log b).
        index = _bucket_index(edges, float(value))
        counts[index] += 1
    return Histogram(edges, tuple(counts), log_scale)


def _bucket_index(edges: tuple[float, ...], value: float) -> int:
    return min(max(bisect.bisect_right(edges, value) - 1, 0), len(edges) - 2)
