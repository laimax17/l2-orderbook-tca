"""Hour-partitioned Parquet writer.

Rows are buffered per partition and flushed as whole row groups. Buffering is
what makes Parquet worth using here: a per-row write would produce columnar
files with one-row row groups, which is strictly worse than JSONL on both size
and scan speed.

The writer is deliberately synchronous and single-threaded. It sits behind the
recorder, not in front of it -- the JSONL capture is the thing that must never
block on the ingest path, and Parquet is produced from it afterwards.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from types import TracebackType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from l2tca.io.schema import SCHEMA_VERSION, TABLES, TableSpec, partition_of

__all__ = ["PartitionedParquetWriter", "SchemaDriftError"]


class SchemaDriftError(RuntimeError):
    """Existing files in a partition were written under a different schema."""


class PartitionedParquetWriter:
    """Buffer rows and write them into ``root/<table>/symbol=/date=/hour=/``.

    Args:
        root: Base directory, e.g. ``data/parquet``.
        table: Table name (``tick``, ``snapshot``, ``signal``) or a
            :class:`TableSpec`.
        symbol_token: Filesystem-safe symbol, e.g. ``BTC-USD``.
        max_rows_per_flush: Rows buffered per partition before a file is
            written. The default targets files in the tens of megabytes for the
            tick table, which is comfortably above the point where Parquet's
            per-file overhead stops mattering.
        compression: ``zstd`` by default -- materially smaller than snappy on
            book data, which is highly repetitive, and fast enough that the
            writer stays IO-bound.
    """

    def __init__(
        self,
        root: Path | str,
        table: str | TableSpec,
        symbol_token: str,
        *,
        max_rows_per_flush: int = 250_000,
        compression: str = "zstd",
    ) -> None:
        spec = TABLES[table] if isinstance(table, str) else table
        self.root = Path(root)
        self.spec = spec
        self.symbol_token = symbol_token
        self.max_rows_per_flush = max(1, max_rows_per_flush)
        self.compression = compression
        self.rows_written = 0
        self.files_written = 0
        self._buffers: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = defaultdict(list)
        self._part_index: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)
        self._checked: set[tuple[tuple[str, str], ...]] = set()
        self._closed = False

    # -- api ---------------------------------------------------------------

    def write_row(self, row: dict[str, Any]) -> None:
        self.write_rows((row,))

    def write_rows(self, rows: object) -> None:
        """Buffer an iterable of row dicts, flushing partitions as they fill."""
        if self._closed:
            raise RuntimeError("writer is closed")
        for row in rows:  # type: ignore[union-attr]
            row = dict(row)
            row.setdefault("schema_version", SCHEMA_VERSION)
            row.setdefault("symbol", self.symbol_token)
            key = partition_of(self.symbol_token, int(row[self.spec.time_column]))
            buffer = self._buffers[key]
            buffer.append(row)
            # Checked per row, not per batch: a single large batch would
            # otherwise land in one oversized file and the row-count cap would
            # stop bounding anything.
            if len(buffer) >= self.max_rows_per_flush:
                self._flush_partition(key)

    def flush(self) -> None:
        """Write every non-empty buffer to disk."""
        for key in list(self._buffers):
            self._flush_partition(key)

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True

    def __enter__(self) -> PartitionedParquetWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internals ---------------------------------------------------------

    def _partition_dir(self, key: tuple[tuple[str, str], ...]) -> Path:
        path = self.root / self.spec.name
        for name, value in key:
            path = path / f"{name}={value}"
        return path

    def _check_existing(self, directory: Path, key: tuple[tuple[str, str], ...]) -> None:
        """Refuse to append into a partition written under a different schema.

        Appending is the right default -- a rerun should not clobber an earlier
        capture -- but it is only safe while the columns are unchanged. Add one
        and the directory holds two shapes; a scan reads the schema of whichever
        file it opens first and rejects the other, at read time, in a message
        about a file rather than about the rerun that caused it.

        Checked once per partition, on its first flush, against one existing
        file: every file in a directory was written by this class, so they agree
        with each other or the run that mixed them already failed here.
        """
        if key in self._checked:
            return
        self._checked.add(key)
        existing = sorted(directory.glob("*.parquet"))
        if not existing:
            return

        found = set(pq.read_schema(existing[0]).names)
        expected = set(self.spec.schema.names)
        if found == expected:
            return
        missing = sorted(expected - found)
        extra = sorted(found - expected)
        raise SchemaDriftError(
            f"{existing[0]} was written under a different schema for the "
            f"{self.spec.name!r} table"
            + (f"; it lacks {missing}" if missing else "")
            + (f"; it has unexpected {extra}" if extra else "")
            + f". Appending would leave two shapes in one partition, which fails "
            f"at read time. Remove {self.root / self.spec.name} and write it again."
        )

    def _flush_partition(self, key: tuple[tuple[str, str], ...]) -> None:
        rows = self._buffers.get(key)
        if not rows:
            return
        directory = self._partition_dir(key)
        directory.mkdir(parents=True, exist_ok=True)

        self._check_existing(directory, key)

        index = self._part_index[key]
        # Never overwrite: a rerun against the same root appends new parts rather
        # than clobbering an existing capture.
        while (path := directory / f"part-{index:05d}.parquet").exists():
            index += 1
        self._part_index[key] = index + 1

        table = _rows_to_table(rows, self.spec.schema)
        pq.write_table(table, path, compression=self.compression)

        self.rows_written += len(rows)
        self.files_written += 1
        self._buffers[key] = []


def _rows_to_table(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    """Build a column-oriented Arrow table from row dicts against a fixed schema.

    Columns are assembled explicitly rather than via ``pa.Table.from_pylist`` so
    that a missing key becomes an explicit null (or a clear error for a
    non-nullable field) instead of silently changing the inferred schema.
    """
    columns = []
    for field in schema:
        values = [row.get(field.name) for row in rows]
        if not field.nullable and any(v is None for v in values):
            missing = next(i for i, v in enumerate(values) if v is None)
            raise ValueError(
                f"column {field.name!r} is not nullable but row {missing} has no value"
            )
        columns.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(columns, schema=schema)
