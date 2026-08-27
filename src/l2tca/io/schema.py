"""Arrow schemas for the three stored tables, and the partition rule.

Design decisions worth defending
--------------------------------
*Explicit Arrow schemas, not inference.* Every writer is constructed with a
fixed :class:`pyarrow.Schema`. Inferred schemas drift -- an all-null column in
one hour becomes ``null`` type, the next hour it is ``double``, and the two
files no longer scan together. Pinning the schema turns that into a write-time
error instead of a read-time surprise a month later.

*A ``schema_version`` column in the data, not just the path.* Version in the
directory name is lost the moment a file is copied. In the row it survives
everything, and a reader can refuse a file it does not understand.

*Nanosecond integers, not timestamps.* Timestamps are stored as ``int64``
nanoseconds rather than Arrow ``timestamp`` types. ``recv_ns`` comes from
``perf_counter_ns`` and has no epoch, so calling it a timestamp would be a lie;
keeping both clocks as plain integers keeps the arithmetic honest and lets
Polars cast on read when a real datetime is wanted.

*``float64`` at this boundary and only here.* Prices are exact ``Decimal`` all
the way through the book. They become floats on the way into Parquet because
that is what the analysis tools want. Arrow ``decimal128`` is available if
exactness ever needs to survive the round trip -- the schema is the one place
that decision has to be made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pyarrow as pa

__all__ = [
    "SCHEMA_VERSION",
    "TABLES",
    "TableSpec",
    "partition_of",
    "signal_schema",
    "snapshot_schema",
    "tick_schema",
]

#: Bumped on any incompatible change to any table below. Written into every row.
SCHEMA_VERSION = 1


def tick_schema() -> pa.Schema:
    """One row per price level touched by a book frame.

    The narrowest faithful representation of the feed: replaying these rows in
    ``seq`` order reproduces the book exactly, so this table is the durable
    substitute for the raw JSONL once a capture has been validated.
    """
    return pa.schema(
        [
            pa.field("schema_version", pa.int32(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("seq", pa.int64(), nullable=False),
            pa.field("recv_ns", pa.int64(), nullable=False),
            pa.field("recv_wall_ns", pa.int64(), nullable=False),
            pa.field("exchange_ts_ns", pa.int64(), nullable=True),
            pa.field("frame_type", pa.string(), nullable=False),  # snapshot | update
            pa.field("side", pa.string(), nullable=False),  # bid | ask
            pa.field("price", pa.float64(), nullable=False),
            pa.field("qty", pa.float64(), nullable=False),
            pa.field("is_delete", pa.bool_(), nullable=False),
            pa.field("checksum", pa.int64(), nullable=True),
        ]
    )


def snapshot_schema() -> pa.Schema:
    """One row per reconstructed book view, depth held in list columns.

    Four parallel lists rather than a long table of levels. A 100-level book at
    a few hundred updates a second would be tens of thousands of rows a second
    in long form, and every analysis would begin with a group-by to put the book
    back together. List columns keep one row per instant, which is the unit
    every downstream question is actually asked in, and they compress well
    because adjacent snapshots differ in only a few levels.
    """
    return pa.schema(
        [
            pa.field("schema_version", pa.int32(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("book_seq", pa.int64(), nullable=False),
            pa.field("recv_ns", pa.int64(), nullable=False),
            pa.field("recv_wall_ns", pa.int64(), nullable=False),
            pa.field("exchange_ts_ns", pa.int64(), nullable=True),
            pa.field("checksum_ok", pa.bool_(), nullable=True),
            pa.field("bid_px", pa.list_(pa.float64()), nullable=False),
            pa.field("bid_qty", pa.list_(pa.float64()), nullable=False),
            pa.field("ask_px", pa.list_(pa.float64()), nullable=False),
            pa.field("ask_qty", pa.list_(pa.float64()), nullable=False),
        ]
    )


def signal_schema() -> pa.Schema:
    """One row per signal value: long format, so new factors need no migration."""
    return pa.schema(
        [
            pa.field("schema_version", pa.int32(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("book_seq", pa.int64(), nullable=False),
            pa.field("recv_ns", pa.int64(), nullable=False),
            pa.field("recv_wall_ns", pa.int64(), nullable=False),
            pa.field("name", pa.string(), nullable=False),
            pa.field("value", pa.float64(), nullable=False),
            pa.field("levels", pa.int32(), nullable=False),
        ]
    )


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Everything the writer and reader need to know about one table."""

    name: str
    schema: pa.Schema
    #: Column holding the wall-clock nanoseconds that drive hour partitioning.
    time_column: str = "recv_wall_ns"

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.schema.names)


TABLES: dict[str, TableSpec] = {
    "tick": TableSpec("tick", tick_schema()),
    "snapshot": TableSpec("snapshot", snapshot_schema()),
    "signal": TableSpec("signal", signal_schema()),
}


def partition_of(symbol_token: str, wall_ns: int) -> tuple[tuple[str, str], ...]:
    """Hive partition key for a row: ``symbol``, UTC ``date``, UTC ``hour``.

    Hive-style ``key=value`` directories so Polars and DuckDB can both prune on
    them without a manifest. Hourly is the finest granularity that keeps files
    large enough to be worth compressing -- a busy hour of ticks is tens of
    megabytes, a busy minute would be hundreds of tiny files and a slow scan.

    UTC throughout, deliberately: a local-time partition column silently
    produces a duplicated and a missing hour twice a year.
    """
    dt = datetime.fromtimestamp(wall_ns / 1e9, tz=UTC)
    return (
        ("symbol", symbol_token),
        ("date", dt.strftime("%Y-%m-%d")),
        ("hour", dt.strftime("%H")),
    )
