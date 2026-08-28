"""``l2tca plot`` -- render a figure to a PNG."""

from __future__ import annotations

import argparse
import sys

from l2tca.config import Paths

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    from l2tca.plot import plot_depth_snapshot, plot_latency_histogram, plot_spread_series

    root = args.root or Paths().parquet
    try:
        if args.kind == "latency":
            if args.report is None:
                print("plot latency needs --report (from `l2tca bench --json`)", file=sys.stderr)
                return 2
            fig = plot_latency_histogram(args.report, stage=args.stage)
        elif args.kind == "depth":
            fig = plot_depth_snapshot(root, symbol_token=args.symbol_token)
        else:
            fig = plot_spread_series(root, symbol_token=args.symbol_token)
    except (FileNotFoundError, ValueError) as exc:
        print(f"cannot plot: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=140)
    print(f"wrote {args.out}")
    return 0
