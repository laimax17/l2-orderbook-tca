"""What did real trades actually pay, measured against the book that stood at the time.

The rest of :mod:`l2tca.tca` measures a *hypothetical* execution: you supply a
fill list and it decomposes the cost. This measures observed ones -- every print
the venue reported -- which needs no assumptions about an order that was never
sent.

Three numbers, and one identity that ties them together::

    effective = 2 * d * (P_fill - M_t)    / M_t * 10_000
    realized  = 2 * d * (P_fill - M_t+h)  / M_t * 10_000
    impact    = 2 * d * (M_t+h  - M_t)    / M_t * 10_000

    effective == realized + impact        exactly, by construction

``d`` is +1 when the taker bought and -1 when it sold, so a positive number is
always a cost to the taker and a gain to whoever was resting. The factor of two
is convention: the spread is a round trip and these are quoted as full spreads.

What each one says:

- **Effective spread** is what the trade actually paid relative to the fair
  price at that instant. Compare it to the *quoted* spread -- the advertised
  price -- and the ratio says whether trades are getting filled inside the touch
  or walking through levels.
- **Realized spread** is what the resting side still had after the market
  finished reacting. It is the part of the spread that was genuinely earned.
- **Impact** is the rest: the market moved against the resting side because the
  trade carried information. This is adverse selection, and separating it from
  revenue is the whole reason the decomposition exists.
"""

from __future__ import annotations

import polars as pl

__all__ = ["TOUCH_TOLERANCE_BPS", "execution_costs", "summarise_costs"]

#: Horizons at which the realized/impact split is reported. The choice matters:
#: too short and the market has not finished reacting, too long and unrelated
#: news is being attributed to this trade. Seconds is the usual range.
DEFAULT_HORIZONS_NS = (1_000_000_000, 5_000_000_000, 30_000_000_000)


def _mid_after(book: pl.DataFrame, horizon_ns: int, *, time_column: str) -> pl.DataFrame:
    """Add ``mid_future``: the mid ``horizon_ns`` later, null past the sample end."""
    last_ns = book.get_column(time_column).max()
    shifted = book.select(
        (pl.col(time_column) - horizon_ns).alias(time_column),
        pl.col("mid").alias("mid_future"),
    )
    return book.join_asof(shifted, on=time_column, strategy="backward").with_columns(
        pl.when(pl.col(time_column) + horizon_ns > last_ns)
        .then(None)
        .otherwise(pl.col("mid_future"))
        .alias("mid_future")
    )


def execution_costs(
    trades: pl.DataFrame,
    book: pl.DataFrame,
    *,
    horizon_ns: int = 5_000_000_000,
    time_column: str = "recv_ns",
) -> pl.DataFrame:
    """One row per observed trade, with its cost against the contemporaneous book.

    Args:
        trades: The ``trade`` table. Rows with ``frame_type == "snapshot"`` are
            dropped -- see below.
        book: One row per book state, with ``mid`` and ``quoted_spread_bps``.
            :func:`l2tca.research.signals_wide` produces this from the ``signal``
            table.
        horizon_ns: How long to wait before reading the mid again, for the
            realized/impact split.

    Two alignment decisions, both of which change every number here:

    **Matched on ``recv_ns``, not on exchange time.** Both tables were stamped
    by one clock in one process as the frames arrived, so a comparison between
    them is a comparison of things this process actually saw in that order.
    Exchange timestamps come from two different channels and carry the venue's
    own queuing, so the two are not reliably comparable to each other.

    **The subscribe backfill is dropped.** Those prints executed before the
    connection existed and all carry the arrival time of the backfill frame, so
    every one of them would be matched against the same book state -- one that
    post-dates them by up to half a minute. Keeping them would not add data, it
    would add fiction. On a 58-second probe capture they were 50 of 185 rows.

    A trade with no book state at or before it is dropped: the alternative is
    reaching forward to a book that had not arrived, which flatters every
    number it touches.
    """
    live = trades.filter(pl.col("frame_type") == "update").sort(time_column)
    if live.is_empty():
        raise ValueError("no live trades; was the capture recorded with --trades?")

    reference = _mid_after(
        book.sort(time_column).select([time_column, "mid", "quoted_spread_bps"]),
        horizon_ns,
        time_column=time_column,
    )

    joined = (
        live.select([time_column, "side", "price", "qty"])
        .join_asof(reference, on=time_column, strategy="backward")
        .drop_nulls(["mid"])
    )

    direction = pl.when(pl.col("side") == "buy").then(1.0).otherwise(-1.0)
    scale = 2 * direction / pl.col("mid") * 10_000
    return joined.with_columns(
        ((pl.col("price") - pl.col("mid")) * scale).alias("effective_bps"),
        ((pl.col("price") - pl.col("mid_future")) * scale).alias("realized_bps"),
        ((pl.col("mid_future") - pl.col("mid")) * scale).alias("impact_bps"),
        (pl.col("price") * pl.col("qty")).alias("notional"),
    )


#: Two effective spreads within this many basis points of each other are treated
#: as equal. A trade at the touch pays *exactly* the quoted spread -- the algebra
#: is an identity -- but the two sides reach it by different float paths, so a
#: strict comparison splits the identical case roughly in half and reports
#: nonsense price improvement. On the six-hour capture that was 77% of trades.
TOUCH_TOLERANCE_BPS = 1e-6


def summarise_costs(costs: pl.DataFrame) -> pl.DataFrame:
    """Size-weighted summary of :func:`execution_costs`, plus the shares worth knowing.

    Weighted by notional rather than counted per trade. A venue's prints are
    mostly tiny and occasionally large; an unweighted mean describes the tiny
    ones, and it is the large ones that carry the cost.

    **Read the median beside the mean.** The distribution is not close to
    symmetric: most trades take the touch and pay exactly the quoted spread,
    while a thin tail executes when the book is momentarily wide. The mean
    describes the tail, the median describes the typical trade, and quoting
    either alone misleads.

    ``at_touch_share`` is the fraction of notional paying the quoted spread
    within a tolerance; ``price_improvement_share`` paid less, and
    ``through_touch_share`` more. The tolerance is not cosmetic -- see
    :data:`TOUCH_TOLERANCE_BPS`.
    """
    if costs.is_empty():
        raise ValueError("no trades to summarise")

    gap = pl.col("effective_bps") - pl.col("quoted_spread_bps")
    at_touch = gap.abs() <= TOUCH_TOLERANCE_BPS

    def weighted(column: str) -> pl.Expr:
        usable = pl.col(column).is_not_null()
        return (
            (pl.col(column) * pl.col("notional")).filter(usable).sum()
            / pl.col("notional").filter(usable).sum()
        ).alias(column.replace("_bps", "_bps_vw"))

    return costs.select(
        pl.len().alias("trades"),
        pl.col("qty").sum().alias("volume"),
        pl.col("notional").sum().alias("notional"),
        weighted("quoted_spread_bps"),
        weighted("effective_bps"),
        weighted("realized_bps"),
        weighted("impact_bps"),
        pl.col("effective_bps").median().alias("effective_bps_median"),
        (at_touch * pl.col("notional"))
        .sum()
        .truediv(pl.col("notional").sum())
        .alias("at_touch_share"),
        ((~at_touch & (gap < 0)) * pl.col("notional"))
        .sum()
        .truediv(pl.col("notional").sum())
        .alias("price_improvement_share"),
        ((~at_touch & (gap > 0)) * pl.col("notional"))
        .sum()
        .truediv(pl.col("notional").sum())
        .alias("through_touch_share"),
        pl.col("realized_bps").is_null().sum().alias("no_horizon"),
    )
