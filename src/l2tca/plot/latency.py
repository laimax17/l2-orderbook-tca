"""Latency histogram from a benchmark report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from l2tca.plot._mpl import pyplot

__all__ = ["plot_latency_histogram"]


def plot_latency_histogram(report: dict | Path | str, *, stage: str | None = None) -> Any:
    """Plot one stage's latency distribution.

    Args:
        report: A :meth:`BenchReport.to_dict` mapping, or a path to the JSON
            written by ``l2tca bench --json``.
        stage: Stage name. ``None`` takes the first stage that has samples,
            which is the end-to-end one whenever it was measurable.

    The x axis is log-scaled to match the bucket edges. A latency distribution
    spans orders of magnitude with a long right tail; on a linear axis the tail
    -- the part that decides whether a strategy reads a stale book -- collapses
    against the right edge.

    Returns:
        The matplotlib ``Figure``.

    Raises:
        ValueError: No stage with samples, or the named stage has none.
    """
    if isinstance(report, str | Path):
        report = json.loads(Path(report).read_text())

    stages = [s for s in report.get("stages", []) if s.get("count")]
    if not stages:
        raise ValueError("report has no stage with samples (is the book implemented?)")
    if stage is None:
        chosen = stages[0]
    else:
        chosen = next((s for s in stages if s["name"] == stage), None)
        if chosen is None:
            raise ValueError(f"no samples for stage {stage!r}")

    hist = chosen["histogram"]
    edges_us = [e / 1000 for e in hist["edges_ns"]]
    counts = hist["counts"]
    widths = [edges_us[i + 1] - edges_us[i] for i in range(len(counts))]

    plt = pyplot()
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(edges_us[:-1], counts, width=widths, align="edge", edgecolor="none")
    if hist.get("log_scale"):
        ax.set_xscale("log")

    percentiles = chosen.get("percentiles", {})
    for label in ("p50", "p99"):
        value = percentiles.get(label)
        if value:
            ax.axvline(value / 1000, linestyle="--", linewidth=1)
            ax.annotate(label, (value / 1000, max(counts) * 0.92), fontsize=8)

    ax.set_xlabel("latency (microseconds, log scale)")
    ax.set_ylabel("count")
    ax.set_title(f"{chosen['name']} -- {chosen['count']} samples")
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    return fig
