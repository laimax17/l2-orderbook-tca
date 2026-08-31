"""Does a signal predict the next move? Measured, with the traps named.

Everything here operates on Polars frames read from the ``signal`` table, so the
evaluation runs on stored data rather than on a live book. That is deliberate:
an evaluation that can only be run by replaying a capture cannot be re-run by
anyone reading the results.

What these functions do not do
------------------------------
They do not establish that a signal is tradeable. Every number produced here is
in-sample, on one symbol, gross of fees and of the spread that would have to be
crossed to act on it -- and the spread is the first thing that eats a
touch-level signal. They measure association between a factor and a subsequent
mid move. That is the beginning of the question, not the answer to it.
"""

from __future__ import annotations

import polars as pl

__all__ = [
    "bucket_summary",
    "forward_return_bps",
    "information_coefficient",
    "signals_wide",
]


def signals_wide(signals: pl.DataFrame) -> pl.DataFrame:
    """Pivot the long ``signal`` table to one row per book state.

    The table is stored long so a new factor needs no migration. Evaluation
    wants the opposite shape -- every factor for one instant on one row -- so
    the pivot happens here rather than in the schema.

    Imbalance appears once per depth, as ``imbalance_1``, ``imbalance_5``, and
    so on; factors defined only at the touch keep their bare name.
    """
    named = signals.with_columns(
        pl.when(pl.col("name") == "imbalance")
        .then(pl.col("name") + "_" + pl.col("levels").cast(pl.Utf8))
        .otherwise(pl.col("name"))
        .alias("factor")
    )
    return named.pivot(
        on="factor", index=["book_seq", "recv_ns"], values="value", aggregate_function="first"
    ).sort("recv_ns")


def forward_return_bps(
    frame: pl.DataFrame,
    horizon_ns: int,
    *,
    price_column: str = "mid",
    time_column: str = "recv_ns",
) -> pl.DataFrame:
    """Add ``forward_bps``: the mid move over ``horizon_ns``, in basis points.

    Matched on time rather than on row count. A book that updates fifty times a
    second and one that updates twice are the same market, and "ten rows ahead"
    means ten milliseconds in one and five seconds in the other -- which turns a
    horizon into a measure of how busy the feed was.

    The last rows of the frame have no counterpart a full horizon ahead. They
    come back null rather than clipped to the end of the sample, because
    clipping would silently shorten the horizon exactly where a trend is most
    likely to have run out.
    """
    if horizon_ns <= 0:
        raise ValueError(f"horizon must be positive, got {horizon_ns}")

    ordered = frame.sort(time_column)
    if ordered.is_empty():
        return ordered.with_columns(pl.lit(None, dtype=pl.Float64).alias("forward_bps"))
    last_ns = ordered.get_column(time_column).max()

    future = ordered.select(
        (pl.col(time_column) - horizon_ns).alias(time_column),
        pl.col(price_column).alias("_future_price"),
    )
    # join_asof with a backward strategy takes, for each row, the latest
    # observation at or before t + horizon -- the same "contemporaneous" rule
    # the TCA module uses, applied forwards.
    joined = ordered.join_asof(future, on=time_column, strategy="backward")

    # Past the end of the sample that rule degrades silently: it keeps matching
    # the final observation, so the horizon shortens towards zero and the last
    # rows report a move measured over less and less time. Null them instead.
    return joined.with_columns(
        pl.when(pl.col("_future_price").is_null() | (pl.col(time_column) + horizon_ns > last_ns))
        .then(None)
        .otherwise((pl.col("_future_price") - pl.col(price_column)) / pl.col(price_column) * 10_000)
        .alias("forward_bps")
    ).drop("_future_price")


def bucket_summary(
    frame: pl.DataFrame,
    signal_column: str,
    *,
    buckets: int = 10,
    return_column: str = "forward_bps",
) -> pl.DataFrame:
    """Mean forward return per signal quantile, with the count and spread.

    Quantiles rather than equal-width bins: a signal whose mass sits in a narrow
    band would put every observation in one equal-width bin and look flat.

    ``std`` and ``n`` are returned alongside the mean and are not decoration. A
    monotone column of means across ten buckets is the shape everyone hopes for,
    and it means nothing if the standard error of each is larger than the
    distance between them.
    """
    if buckets < 2:
        raise ValueError(f"need at least two buckets, got {buckets}")

    usable = frame.drop_nulls([signal_column, return_column])
    if usable.is_empty():
        raise ValueError(f"no rows with both {signal_column} and {return_column}")

    ranked = usable.with_columns(
        (pl.col(signal_column).rank("ordinal") * buckets / pl.len())
        .ceil()
        .clip(1, buckets)
        .cast(pl.Int32)
        .alias("bucket")
    )
    return (
        ranked.group_by("bucket")
        .agg(
            pl.len().alias("n"),
            pl.col(signal_column).mean().alias("signal_mean"),
            pl.col(return_column).mean().alias("forward_mean"),
            pl.col(return_column).std().alias("forward_std"),
        )
        .with_columns((pl.col("forward_std") / pl.col("n").sqrt()).alias("forward_stderr"))
        .sort("bucket")
    )


def information_coefficient(
    frame: pl.DataFrame,
    signal_column: str,
    *,
    return_column: str = "forward_bps",
) -> float:
    """Spearman rank correlation between the signal and the forward return.

    Rank rather than Pearson because both series are heavy-tailed: a handful of
    large moves would otherwise decide the number, and what is being asked is
    whether the ordering carries information, not whether the outliers line up.

    One number for a whole sample hides regime changes. Read it next to
    :func:`bucket_summary`, never instead of it.
    """
    usable = frame.drop_nulls([signal_column, return_column])
    if usable.height < 3:
        raise ValueError(f"need at least three paired observations, got {usable.height}")
    return usable.select(
        pl.corr(pl.col(signal_column), pl.col(return_column), method="spearman")
    ).item()
