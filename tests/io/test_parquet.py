"""Storage layer: schemas, hour partitioning, and validation on read."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from tests.conftest import SNAPSHOT_FRAME, UPDATE_FRAME
from tests.factories import raw_messages, trade_frame, write_capture

from l2tca.io.convert import iter_tick_rows, iter_trade_rows
from l2tca.io.reader import ParquetValidationError, read_table, scan_table, validate_frame
from l2tca.io.schema import SCHEMA_VERSION, TABLES, partition_of
from l2tca.io.writer import PartitionedParquetWriter

HOUR_NS = 3_600 * 1_000_000_000
BASE_NS = 1_767_346_200_000_000_000  # 2026-01-02T09:30:00Z


def _tick_row(wall_ns: int, seq: int = 0, price: float = 64_000.0) -> dict:
    return {
        "seq": seq,
        "recv_ns": 1_000 + seq,
        "recv_wall_ns": wall_ns,
        "exchange_ts_ns": None,
        "frame_type": "update",
        "side": "bid",
        "price": price,
        "qty": 1.25,
        "is_delete": False,
        "checksum": 12345,
    }


def test_partition_key_is_utc_symbol_date_hour() -> None:
    assert partition_of("BTC-USD", BASE_NS) == (
        ("symbol", "BTC-USD"),
        ("date", "2026-01-02"),
        ("hour", "09"),
    )


def test_rows_land_in_hourly_directories(tmp_path: Path) -> None:
    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
        writer.write_rows([_tick_row(BASE_NS + i * HOUR_NS // 2, seq=i) for i in range(6)])

    hours = sorted(
        p.name for p in (tmp_path / "tick" / "symbol=BTC-USD" / "date=2026-01-02").iterdir()
    )
    # 09:30, 10:00, 10:30, 11:00, 11:30, 12:00 UTC
    assert hours == ["hour=09", "hour=10", "hour=11", "hour=12"]
    assert writer.rows_written == 6
    assert writer.files_written == 4
    assert read_table(tmp_path, "tick").height == 6


def test_round_trip_preserves_values_and_dtypes(tmp_path: Path) -> None:
    rows = [_tick_row(BASE_NS + i, seq=i, price=64_000.0 + i) for i in range(10)]
    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
        writer.write_rows(rows)

    frame = read_table(tmp_path, "tick")
    assert frame.height == 10
    assert frame.get_column("price").to_list() == [r["price"] for r in rows]
    assert frame.schema["seq"] == pl.Int64
    assert frame.schema["schema_version"] == pl.Int32
    assert frame.get_column("schema_version").unique().to_list() == [SCHEMA_VERSION]


def test_schema_version_is_stamped_without_being_supplied(tmp_path: Path) -> None:
    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
        writer.write_row(_tick_row(BASE_NS))
    assert read_table(tmp_path, "tick").get_column("schema_version")[0] == SCHEMA_VERSION


def test_snapshot_table_stores_depth_as_list_columns(tmp_path: Path) -> None:
    with PartitionedParquetWriter(tmp_path, "snapshot", "BTC-USD") as writer:
        writer.write_row(
            {
                "book_seq": 1,
                "recv_ns": 10,
                "recv_wall_ns": BASE_NS,
                "exchange_ts_ns": None,
                "checksum_ok": True,
                "bid_px": [64_000.0, 63_999.9],
                "bid_qty": [1.0, 2.0],
                "ask_px": [64_000.1, 64_000.2],
                "ask_qty": [3.0, 4.0],
            }
        )

    frame = read_table(tmp_path, "snapshot")
    assert frame.schema["bid_px"] == pl.List(pl.Float64)
    assert frame.get_column("bid_px")[0].to_list() == [64_000.0, 63_999.9]


def test_signal_table_is_long_format(tmp_path: Path) -> None:
    with PartitionedParquetWriter(tmp_path, "signal", "BTC-USD") as writer:
        writer.write_rows(
            [
                {"book_seq": 1, "recv_ns": 1, "recv_wall_ns": BASE_NS,
                 "name": "obi", "value": 0.25, "levels": 1},
                {"book_seq": 1, "recv_ns": 1, "recv_wall_ns": BASE_NS,
                 "name": "micro_price", "value": 64_000.05, "levels": 0},
            ]
        )
    assert set(read_table(tmp_path, "signal").get_column("name")) == {"obi", "micro_price"}


def test_a_missing_non_nullable_value_fails_at_write_time(tmp_path: Path) -> None:
    """Better a loud write error than an inferred schema that drifts between hours."""
    row = _tick_row(BASE_NS)
    del row["side"]
    writer = PartitionedParquetWriter(tmp_path, "tick", "BTC-USD")
    writer.write_row(row)
    with pytest.raises(ValueError, match="not nullable"):
        writer.close()


def test_reruns_append_parts_instead_of_overwriting(tmp_path: Path) -> None:
    for _ in range(3):
        with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
            writer.write_row(_tick_row(BASE_NS))

    directory = tmp_path / "tick" / "symbol=BTC-USD" / "date=2026-01-02" / "hour=09"
    assert sorted(p.name for p in directory.iterdir()) == [
        "part-00000.parquet",
        "part-00001.parquet",
        "part-00002.parquet",
    ]
    assert read_table(tmp_path, "tick").height == 3


def test_buffer_flushes_when_it_fills(tmp_path: Path) -> None:
    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD", max_rows_per_flush=4) as writer:
        writer.write_rows([_tick_row(BASE_NS, seq=i) for i in range(10)])
        assert writer.files_written == 2  # 8 rows flushed, 2 still buffered
    assert writer.files_written == 3
    assert read_table(tmp_path, "tick").height == 10


def test_scan_prunes_by_symbol(tmp_path: Path) -> None:
    for token in ("BTC-USD", "ETH-USD"):
        with PartitionedParquetWriter(tmp_path, "tick", token) as writer:
            writer.write_row(_tick_row(BASE_NS))

    assert scan_table(tmp_path, "tick").collect().height == 2
    assert scan_table(tmp_path, "tick", symbol_token="ETH-USD").collect().height == 1


def test_validation_rejects_a_future_schema_version(tmp_path: Path) -> None:
    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
        writer.write_row({**_tick_row(BASE_NS), "schema_version": SCHEMA_VERSION + 1})
    with pytest.raises(ParquetValidationError, match="schema_version"):
        read_table(tmp_path, "tick")


def test_validation_rejects_a_missing_column() -> None:
    frame = pl.DataFrame({"schema_version": [1], "symbol": ["BTC-USD"]})
    with pytest.raises(ParquetValidationError, match="missing columns"):
        validate_frame(frame, TABLES["tick"])


def test_validation_rejects_a_narrowed_dtype(tmp_path: Path) -> None:
    """Polars will read an int32 where int64 was declared; arithmetic then overflows."""
    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
        writer.write_row(_tick_row(BASE_NS))
    frame = read_table(tmp_path, "tick").with_columns(pl.col("seq").cast(pl.Int32))
    with pytest.raises(ParquetValidationError, match=r"tick\.seq"):
        validate_frame(frame, "tick")


def test_missing_dataset_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no tick table"):
        scan_table(tmp_path, "tick")


def test_capture_converts_to_ticks_end_to_end(tmp_path: Path, capture: Path) -> None:
    rows = list(iter_tick_rows(capture))
    assert rows, "the capture should carry book frames"
    assert {r["frame_type"] for r in rows} == {"snapshot", "update"}
    assert any(r["is_delete"] for r in rows), "deletes must survive the conversion"

    with PartitionedParquetWriter(tmp_path, "tick", "BTC-USD") as writer:
        writer.write_rows(rows)

    frame = read_table(tmp_path, "tick")
    assert frame.height == len(rows)
    # A zero quantity is a removal, and the flag records that so no reader has to
    # know the convention or compare a float against zero.
    deletes = frame.filter(pl.col("is_delete"))
    assert deletes.height > 0
    assert deletes.get_column("qty").to_list() == [0.0] * deletes.height


# -- trade table -----------------------------------------------------------


def test_trade_rows_survive_the_round_trip(tmp_path: Path) -> None:
    """Written through the real writer and read back with Polars, like every table."""
    path = write_capture(
        tmp_path / "trades.jsonl",
        raw_messages(
            [
                SNAPSHOT_FRAME,
                trade_frame([("buy", "78012.3", "0.015"), ("buy", "78012.4", "0.2")]),
                UPDATE_FRAME,
                trade_frame([("sell", "78011.9", "1.25")], first_trade_id=3),
            ]
        ),
    )

    rows = list(iter_trade_rows(path))
    assert len(rows) == 3

    root = tmp_path / "pq"
    with PartitionedParquetWriter(root, "trade", rows[0]["symbol"]) as writer:
        writer.write_rows(iter(rows))

    frame = read_table(root, "trade")
    assert frame.height == 3
    assert frame.get_column("side").to_list() == ["buy", "buy", "sell"]
    assert frame.get_column("trade_id").to_list() == [1, 2, 3]
    assert frame.get_column("price").to_list() == [78012.3, 78012.4, 78011.9]
    # The two prints from one taker share an arrival stamp; the third does not.
    stamps = frame.get_column("recv_ns").to_list()
    assert stamps[0] == stamps[1] != stamps[2]


def test_a_book_only_capture_yields_no_trade_rows(tmp_path: Path) -> None:
    """Not an error: most captures in this project are recorded without --trades."""
    path = write_capture(tmp_path / "book.jsonl", raw_messages([SNAPSHOT_FRAME, UPDATE_FRAME]))
    assert list(iter_trade_rows(path)) == []


def test_backfilled_trades_stay_distinguishable_in_parquet(tmp_path: Path) -> None:
    """Provenance is not recoverable on read, so it has to survive the write."""
    path = write_capture(
        tmp_path / "backfill.jsonl",
        raw_messages(
            [
                SNAPSHOT_FRAME,
                trade_frame([("sell", "78914.8", "0.0000004")], snapshot=True),
                trade_frame([("buy", "78883.2", "0.009")], first_trade_id=2),
            ]
        ),
    )
    root = tmp_path / "pq"
    rows = list(iter_trade_rows(path))
    with PartitionedParquetWriter(root, "trade", rows[0]["symbol"]) as writer:
        writer.write_rows(iter(rows))

    frame = read_table(root, "trade")
    assert frame.get_column("frame_type").to_list() == ["snapshot", "update"]
    live = frame.filter(pl.col("frame_type") == "update")
    assert live.height == 1
