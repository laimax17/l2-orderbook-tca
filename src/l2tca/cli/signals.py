"""``l2tca signals`` -- replay a capture into the snapshot and signal tables."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from l2tca.config import Paths
from l2tca.io.derive import DEFAULT_IMBALANCE_LEVELS, iter_derived_rows
from l2tca.io.writer import PartitionedParquetWriter

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    root = args.out or Paths().parquet
    levels = tuple(args.imbalance_levels) or DEFAULT_IMBALANCE_LEVELS

    pairs = iter_derived_rows(
        args.file,
        symbol=args.symbol,
        depth=args.depth,
        levels=args.levels,
        price_precision=args.price_precision,
        qty_precision=args.qty_precision,
        limit=args.limit,
        imbalance_levels=levels,
    )
    first = next(pairs, None)
    if first is None:
        print("no book frames in this capture; nothing written", file=sys.stderr)
        return 1

    snapshot_row, signal_batch = first
    token = snapshot_row["symbol"]
    unverified = 0

    with (
        PartitionedParquetWriter(
            root, "snapshot", token, max_rows_per_flush=args.rows_per_file
        ) as snapshots,
        PartitionedParquetWriter(
            root, "signal", token, max_rows_per_flush=args.rows_per_file
        ) as signals,
    ):
        snapshots.write_row(snapshot_row)
        signals.write_rows(iter(signal_batch))
        unverified += snapshot_row["checksum_ok"] is False
        for snapshot_row, signal_batch in pairs:
            snapshots.write_row(snapshot_row)
            signals.write_rows(iter(signal_batch))
            unverified += snapshot_row["checksum_ok"] is False

    print(f"wrote {snapshots.rows_written} snapshot rows -> {Path(root) / 'snapshot'}")
    print(f"wrote {signals.rows_written} signal rows -> {Path(root) / 'signal'}")
    if unverified:
        # Not a failure. The rows are kept and flagged, because a book the
        # exchange disagreed with is a finding, not a reason to write nothing.
        print(
            f"warning: {unverified} frames failed checksum verification; "
            "their rows carry checksum_ok = false",
            file=sys.stderr,
        )
    return 0
