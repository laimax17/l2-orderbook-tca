"""``l2tca costs`` -- realized execution cost on the trades a capture observed."""

from __future__ import annotations

import argparse
import sys

import polars as pl

from l2tca.config import Paths
from l2tca.io.reader import read_table
from l2tca.research import execution_costs, signals_wide, summarise_costs

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    root = args.root or Paths().parquet
    try:
        trades = read_table(root, "trade")
        book = signals_wide(read_table(root, "signal"))
    except FileNotFoundError as exc:
        print(
            f"{exc}\nRun `l2tca convert` and `l2tca signals` on the capture first.",
            file=sys.stderr,
        )
        return 1

    rows = []
    for horizon_s in args.horizons:
        horizon_ns = int(horizon_s * 1e9)
        try:
            costs = execution_costs(trades, book, horizon_ns=horizon_ns)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        rows.append(summarise_costs(costs).with_columns(pl.lit(horizon_s).alias("horizon_s")))

    summary = pl.concat(rows)
    summary = summary.select(["horizon_s", *[c for c in summary.columns if c != "horizon_s"]])
    with pl.Config(tbl_cols=-1, tbl_width_chars=200, float_precision=4):
        print(summary)

    first = summary.row(0, named=True)
    ratio = first["effective_bps_vw"] / first["quoted_spread_bps_vw"]
    print(
        f"\n{first['trades']:,} trades, {first['notional']:,.0f} quote units of notional"
        f"\neffective / quoted = {ratio:.3f}"
        f"   ({first['price_improvement_share']:.1%} of notional inside the touch)"
    )
    if first["no_horizon"]:
        print(
            f"{first['no_horizon']:,} trades had no book state a full horizon later "
            "and are excluded from the realized/impact columns."
        )
    return 0
