"""``l2tca convert`` -- flatten a capture into the tick and trade Parquet tables."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from l2tca.config import Paths
from l2tca.io.convert import iter_tick_rows, iter_trade_rows
from l2tca.io.writer import PartitionedParquetWriter

__all__ = ["run"]


def _write_table(
    root: Path | str,
    table: str,
    rows: Iterator[dict[str, Any]],
    rows_per_file: int,
) -> tuple[int, int] | None:
    """Drain ``rows`` into ``table``. ``None`` when there were none.

    The first row is pulled before opening the writer so that an empty stream
    leaves no directory behind: an empty partition is indistinguishable from a
    capture that was never converted, and the reader would rather see neither.
    """
    first = next(rows, None)
    if first is None:
        return None
    with PartitionedParquetWriter(
        root, table, first["symbol"], max_rows_per_flush=rows_per_file
    ) as writer:
        writer.write_row(first)
        writer.write_rows(rows)
    return writer.rows_written, writer.files_written


def run(args: argparse.Namespace) -> int:
    root = args.out or Paths().parquet

    ticks = _write_table(
        root, "tick", iter_tick_rows(args.file, limit=args.limit), args.rows_per_file
    )
    if ticks is None:
        print("no book frames in this capture; nothing written", file=sys.stderr)
        return 1
    print(f"wrote {ticks[0]} tick rows across {ticks[1]} files -> {Path(root) / 'tick'}")

    # A second pass over the capture. Book-only recordings are the norm here, so
    # the absence of trades is reported rather than treated as a failure.
    trades = _write_table(root, "trade", iter_trade_rows(args.file), args.rows_per_file)
    if trades is None:
        print("no trade frames in this capture (recorded without --trades)")
    else:
        print(f"wrote {trades[0]} trade rows across {trades[1]} files -> {Path(root) / 'trade'}")
    return 0
