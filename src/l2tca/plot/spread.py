"""Quoted spread over time, from the ``snapshot`` table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from l2tca.io.reader import read_table
from l2tca.plot._mpl import pyplot

__all__ = ["plot_spread_series", "spread_frame"]


def spread_frame(root: Path | str, *, symbol_token: str | None = None) -> pl.DataFrame:
    """Top-of-book spread per stored view, in absolute terms and in bps.

    Split out from the plotting so the derivation can be tested without a
    rendering backend, and reused from a notebook.

    One-sided views are dropped: a spread is not defined for them, and carrying
    a null through would only reappear as a gap in the plot.
    """
    frame = read_table(root, "snapshot", symbol_token=symbol_token)
    return (
        frame.filter(
            (pl.col("bid_px").list.len() > 0) & (pl.col("ask_px").list.len() > 0)
        )
        .with_columns(
            best_bid=pl.col("bid_px").list.first(),
            best_ask=pl.col("ask_px").list.first(),
        )
        .with_columns(
            spread=pl.col("best_ask") - pl.col("best_bid"),
            mid=(pl.col("best_ask") + pl.col("best_bid")) / 2,
        )
        .with_columns(spread_bps=1e4 * pl.col("spread") / pl.col("mid"))
        .sort("recv_wall_ns")
    )


def plot_spread_series(
    root: Path | str,
    *,
    symbol_token: str | None = None,
    in_bps: bool = True,
) -> Any:
    """Plot the quoted spread against wall-clock time.

    Basis points by default so the series stays comparable as the price level
    moves; the absolute spread is not.

    Returns:
        The matplotlib ``Figure``.

    Raises:
        ValueError: No two-sided view in the table.
    """
    frame = spread_frame(root, symbol_token=symbol_token)
    if frame.height == 0:
        raise ValueError("no two-sided snapshot rows to plot")

    seconds = (frame["recv_wall_ns"] - frame["recv_wall_ns"][0]) / 1e9
    values = frame["spread_bps"] if in_bps else frame["spread"]

    plt = pyplot()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(seconds, values, linewidth=0.9)
    ax.set_xlabel("seconds into capture")
    ax.set_ylabel("spread (bps)" if in_bps else "spread")
    ax.set_title(f"{frame['symbol'][0]} quoted spread")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig
