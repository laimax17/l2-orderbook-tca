"""Order book depth ladder at one instant, from the ``snapshot`` table."""

from __future__ import annotations

from itertools import accumulate
from pathlib import Path
from typing import Any

import polars as pl

from l2tca.io.reader import read_table
from l2tca.plot._mpl import pyplot

__all__ = ["plot_depth_snapshot"]


def plot_depth_snapshot(
    root: Path | str,
    *,
    symbol_token: str | None = None,
    book_seq: int | None = None,
    levels: int = 50,
    cumulative: bool = True,
) -> Any:
    """Plot resting quantity against price for one book view.

    Args:
        root: Parquet root, e.g. ``data/parquet``.
        book_seq: Which view to draw. ``None`` takes the last one in the table.
        levels: Cap on levels drawn per side.
        cumulative: Draw cumulative depth (the "staircase" that shows what an
            order walking the book would consume) rather than per-level
            quantity. Per-level is noisier and mostly shows queue churn.

    Returns:
        The matplotlib ``Figure``. The caller saves or shows it.

    Raises:
        ValueError: The snapshot table holds no matching row.
    """
    frame = read_table(root, "snapshot", symbol_token=symbol_token)
    if frame.height == 0:
        raise ValueError("snapshot table is empty")
    if book_seq is None:
        row = frame.sort("book_seq").tail(1)
    else:
        row = frame.filter(pl.col("book_seq") == book_seq)
        if row.height == 0:
            raise ValueError(f"no snapshot row with book_seq={book_seq}")

    record = row.row(0, named=True)
    plt = pyplot()
    fig, ax = plt.subplots(figsize=(9, 5))

    for side, price_col, qty_col in (("bid", "bid_px", "bid_qty"), ("ask", "ask_px", "ask_qty")):
        prices = list(record[price_col])[:levels]
        quantities = list(record[qty_col])[:levels]
        if not prices:
            continue
        if cumulative:
            quantities = list(accumulate(quantities))
        ax.step(prices, quantities, where="post", label=side)
        ax.fill_between(prices, quantities, step="post", alpha=0.25)

    ax.set_xlabel("price")
    ax.set_ylabel("cumulative quantity" if cumulative else "quantity")
    ax.set_title(f"{record['symbol']} depth at book_seq={record['book_seq']}")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig
