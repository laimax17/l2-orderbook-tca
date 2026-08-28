"""``l2tca bench`` -- time recv -> book-updated against a capture."""

from __future__ import annotations

import argparse
import sys

from l2tca.bench.harness import run_book_benchmark
from l2tca.bench.report import format_report

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    report = run_book_benchmark(
        args.file,
        symbol=args.symbol,
        depth=args.depth,
        limit=args.limit,
        warmup=args.warmup,
        view_levels=args.view_levels,
    )
    print(report.to_json() if args.as_json else format_report(report, histograms=args.histogram))
    if any(stage.note == "not implemented" for stage in report.stages):
        print(
            "\nbook stages are unimplemented -- see src/l2tca/book/ and docs/CORE.md",
            file=sys.stderr,
        )
    return 0
