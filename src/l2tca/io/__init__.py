"""Columnar storage: Arrow schemas, hour-partitioned Parquet, Polars readers."""

from l2tca.io.convert import iter_tick_rows, tick_rows_from_frame
from l2tca.io.reader import ParquetValidationError, read_table, scan_table, validate_frame
from l2tca.io.schema import (
    SCHEMA_VERSION,
    TABLES,
    TableSpec,
    partition_of,
    signal_schema,
    snapshot_schema,
    tick_schema,
)
from l2tca.io.writer import PartitionedParquetWriter

__all__ = [
    "SCHEMA_VERSION",
    "TABLES",
    "ParquetValidationError",
    "PartitionedParquetWriter",
    "TableSpec",
    "iter_tick_rows",
    "partition_of",
    "read_table",
    "scan_table",
    "signal_schema",
    "snapshot_schema",
    "tick_rows_from_frame",
    "tick_schema",
    "validate_frame",
]
