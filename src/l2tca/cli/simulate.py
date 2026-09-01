"""``l2tca simulate`` -- replay a TWAP execution across many windows of a capture."""

from __future__ import annotations

import argparse
import statistics
import sys
from decimal import Decimal

from l2tca.book.types import Side
from l2tca.research.backtest import run_windows

__all__ = ["run"]

_LAYERS = ("spread_bps", "timing_bps", "fees_bps", "opportunity_bps", "total_bps")


def _summarise(name: str, values: list[float]) -> str:
    if not values:
        return f"{name:<18}{'--':>10}"
    ordered = sorted(values)
    worst = ordered[-1]
    return (
        f"{name:<18}{statistics.median(values):>10.3f}"
        f"{ordered[0]:>10.3f}{worst:>10.3f}"
        f"{statistics.pstdev(values) if len(values) > 1 else 0.0:>10.3f}"
    )


def run(args: argparse.Namespace) -> int:
    side = Side.BID if args.side == "buy" else Side.ASK
    try:
        rows = list(
            run_windows(
                args.file,
                symbol=args.symbol,
                side=side,
                qty=Decimal(str(args.qty)),
                windows=args.windows,
                duration_ns=int(args.duration * 1e9),
                slices=args.slices,
                depth=args.depth,
            )
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if not rows:
        print("no window produced a usable execution", file=sys.stderr)
        return 1

    filled = [r for r in rows if r.get("fills")]
    print(
        f"{args.side} {args.qty} {args.symbol} over {args.duration:.0f}s, "
        f"{args.slices} slices, {len(rows)} windows across the capture "
        f"({len(filled)} filled)"
    )
    complete = sum(1 for r in filled if r["filled_qty"] >= float(args.qty) - 1e-12)
    print(f"windows filling the full quantity: {complete}/{len(rows)}")

    print(f"\n{'':<18}{'median':>10}{'best':>10}{'worst':>10}{'stdev':>10}   (bps)")
    print("-" * 68)
    for label, key in (("vs arrival", "vs_arrival_bps"), ("vs interval VWAP", "vs_vwap_bps")):
        print(_summarise(label, [r[key] for r in filled if r.get(key) is not None]))
    print()
    for key in _LAYERS:
        print(_summarise(key.replace("_bps", ""), [r[key] for r in filled if key in r]))

    if args.per_window:
        print(f"\n{'window':>7}{'filled':>10}{'avg price':>13}{'vs arrival':>12}{'vs vwap':>10}")
        for i, r in enumerate(rows):
            vwap = r.get("vs_vwap_bps")
            print(
                f"{i:>7}{r['filled_qty']:>10.4f}{r.get('avg_price', float('nan')):>13.2f}"
                f"{r.get('vs_arrival_bps', float('nan')):>12.3f}"
                f"{'--' if vwap is None else f'{vwap:.3f}':>10}"
            )
    return 0
