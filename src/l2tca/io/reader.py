"""Polars readers, with schema validation on the way in.

Reading is where a storage bug surfaces, so every entry point here checks the
file against the declared schema before handing back a frame. A silent dtype
drift that only shows up three joins later is the failure mode this prevents.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from l2tca.io.schema import SCHEMA_VERSION, TABLES, TableSpec

__all__ = ["ParquetValidationError", "read_table", "scan_table", "validate_frame"]


class ParquetValidationError(ValueError):
    """A stored table does not match the schema this build expects."""


def _spec(table: str | TableSpec) -> TableSpec:
    return TABLES[table] if isinstance(table, str) else table


def _dataset_root(root: Path | str, spec: TableSpec) -> Path:
    return Path(root) / spec.name


def scan_table(
    root: Path | str,
    table: str | TableSpec,
    *,
    symbol_token: str | None = None,
) -> pl.LazyFrame:
    """Lazily scan a partitioned table, pruning by symbol when given.

    Lazy by default so a filter on ``date``/``hour`` is pushed down to partition
    pruning and a multi-day dataset never has to be materialised whole.
    """
    spec = _spec(table)
    base = _dataset_root(root, spec)
    if not base.exists():
        raise FileNotFoundError(f"no {spec.name} table under {base}")

    pattern = base / "**" / "*.parquet"
    frame = pl.scan_parquet(str(pattern), hive_partitioning=True)
    if symbol_token is not None:
        frame = frame.filter(pl.col("symbol") == symbol_token)
    return frame


def read_table(
    root: Path | str,
    table: str | TableSpec,
    *,
    symbol_token: str | None = None,
    validate: bool = True,
) -> pl.DataFrame:
    """Materialise a partitioned table, validating it unless told not to."""
    spec = _spec(table)
    frame = scan_table(root, spec, symbol_token=symbol_token).collect()
    if validate:
        validate_frame(frame, spec)
    return frame


def validate_frame(frame: pl.DataFrame, table: str | TableSpec) -> None:
    """Raise unless ``frame`` carries this build's schema for ``table``.

    Checks three things, in order of how badly each one bites:

    1. Every declared column is present. Hive partition columns may add extras
       (``date``, ``hour``), which are tolerated.
    2. ``schema_version`` is a version this build understands. A file written by
       a newer build is refused rather than misread.
    3. Numeric columns carry the declared width. Polars will happily read an
       ``int32`` where ``int64`` was declared; downstream arithmetic then
       overflows quietly.
    """
    spec = _spec(table)
    missing = [c for c in spec.columns if c not in frame.columns]
    if missing:
        raise ParquetValidationError(f"{spec.name}: missing columns {missing}")

    if frame.height:
        versions = frame.get_column("schema_version").unique().to_list()
        unknown = [v for v in versions if v is None or int(v) > SCHEMA_VERSION]
        if unknown:
            raise ParquetValidationError(
                f"{spec.name}: rows written with schema_version {unknown}, "
                f"this build understands up to v{SCHEMA_VERSION}"
            )

    expected = _polars_dtypes(spec)
    for name, dtype in expected.items():
        actual = frame.schema[name]
        if actual != dtype:
            raise ParquetValidationError(
                f"{spec.name}.{name}: expected {dtype}, found {actual}"
            )


def _polars_dtypes(spec: TableSpec) -> dict[str, pl.DataType]:
    """Map the declared Arrow schema onto the Polars dtypes a read should yield."""
    import pyarrow as pa

    mapping: dict[str, pl.DataType] = {}
    for field in spec.schema:
        t = field.type
        if pa.types.is_int32(t):
            mapping[field.name] = pl.Int32()
        elif pa.types.is_int64(t):
            mapping[field.name] = pl.Int64()
        elif pa.types.is_float64(t):
            mapping[field.name] = pl.Float64()
        elif pa.types.is_boolean(t):
            mapping[field.name] = pl.Boolean()
        elif pa.types.is_string(t):
            mapping[field.name] = pl.String()
        elif pa.types.is_list(t) and pa.types.is_float64(t.value_type):
            mapping[field.name] = pl.List(pl.Float64())
    return mapping
