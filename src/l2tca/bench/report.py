"""The benchmark result type and how it renders.

Kept apart from the measurement loop so the loop stays readable and so a report
can be deserialised and re-rendered without importing the harness.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from dataclasses import dataclass, field
from typing import Any

from l2tca.bench.histogram import Histogram
from l2tca.bench.latency import LatencyStats

__all__ = ["BenchReport", "environment", "format_report", "render_histogram"]


def environment() -> dict[str, Any]:
    """Recorded with every run: latency numbers are meaningless without it."""
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
    }


@dataclass(slots=True)
class BenchReport:
    """Everything one benchmark run produced."""

    source: str
    messages: int
    update_frames: int
    snapshot_frames: int
    warmup: int
    elapsed_ns: int
    stages: list[LatencyStats] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)

    @property
    def messages_per_s(self) -> float:
        """Replay throughput end to end, including file IO and JSON decoding."""
        if self.elapsed_ns <= 0:
            return math.nan
        return self.messages / (self.elapsed_ns / 1e9)

    def stage(self, name: str) -> LatencyStats | None:
        return next((s for s in self.stages if s.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "messages": self.messages,
            "update_frames": self.update_frames,
            "snapshot_frames": self.snapshot_frames,
            "warmup": self.warmup,
            "elapsed_ns": self.elapsed_ns,
            "messages_per_s": self.messages_per_s,
            "environment": self.environment,
            "stages": [s.to_dict() for s in self.stages],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _us(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value / 1000:.3f}"


def render_histogram(hist: Histogram, *, width: int = 44) -> str:
    """ASCII bars, one row per bucket, empty leading and trailing rows trimmed."""
    if not hist.counts:
        return "  (no samples)"
    peak = max(hist.counts)
    if peak == 0:
        return "  (no samples)"

    first = next(i for i, c in enumerate(hist.counts) if c)
    last = len(hist.counts) - 1 - next(i for i, c in enumerate(reversed(hist.counts)) if c)

    rows = []
    for i in range(first, last + 1):
        count = hist.counts[i]
        bar = "#" * max(1 if count else 0, round(count / peak * width))
        rows.append(f"  {_us(hist.edges_ns[i]):>10} us |{bar:<{width}}| {count}")
    return "\n".join(rows)


def format_report(report: BenchReport, *, histograms: bool = False) -> str:
    """Fixed-width table in microseconds, optionally with per-stage histograms."""
    header = (
        f"{'stage':<26}{'n':>9}{'p50':>10}{'p90':>10}{'p99':>10}{'p99.9':>10}{'max':>10}{'err':>6}"
    )
    lines = [
        f"source        : {report.source}",
        f"frames        : {report.messages} messages, {report.update_frames} updates, "
        f"{report.snapshot_frames} snapshots (warmup {report.warmup}, snapshots excluded)",
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

    if histograms:
        for stage in report.stages:
            if stage.note or not stage.count:
                continue
            lines.extend(
                ["", f"{stage.name} -- {stage.count} samples", render_histogram(stage.hist)]
            )
    return "\n".join(lines)
