"""``l2tca convert`` -- flatten a capture into the tick Parquet table."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from l2tca.config import Paths
from l2tca.io.convert import iter_tick_rows
from l2tca.io.writer import PartitionedParquetWriter

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    root = args.out or Paths().parquet

    rows = iter_tick_rows(args.file, limit=args.limit)
    first = next(rows, None)
    if first is None:
        print("no book frames in this capture; nothing written", file=sys.stderr)
        return 1

    with PartitionedParquetWriter(
        root, "tick", first["symbol"], max_rows_per_flush=args.rows_per_file
    ) as writer:
        writer.write_row(first)
        writer.write_rows(rows)

    print(
        f"wrote {writer.rows_written} tick rows across {writer.files_written} files "
        f"-> {Path(root) / 'tick'}"
    )
    return 0
