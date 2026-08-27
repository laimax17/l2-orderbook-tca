"""Turn a raw JSONL capture into ``tick`` table rows.

Deliberately independent of :mod:`l2tca.book`: converting a recording to Parquet
must work before a single line of the book is written, so that storage, reading
and validation can all be proven end to end against real captured bytes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from l2tca.config import symbol_to_path_token
from l2tca.feed.messages import BookLevel, BookSnapshot, BookUpdate, RawMessage, parse
from l2tca.feed.replay import iter_raw_messages
from l2tca.io.schema import SCHEMA_VERSION

__all__ = ["iter_tick_rows", "tick_rows_from_frame"]


def _rows_for_side(
    side: str,
    levels: tuple[BookLevel, ...],
    base: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    for level in levels:
        qty = float(level.qty)
        yield {
            **base,
            "side": side,
            "price": float(level.price),
            "qty": qty,
            # Kraken signals removal with a zero quantity; storing the flag
            # explicitly means a reader never has to know that convention, and
            # a float comparison against zero never has to be made downstream.
            "is_delete": qty == 0.0,
        }


def tick_rows_from_frame(
    message: RawMessage,
    frame: BookSnapshot | BookUpdate,
) -> list[dict[str, Any]]:
    """Flatten one parsed book frame into one row per touched price level."""
    base = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol_to_path_token(frame.symbol),
        "seq": message.seq,
        "recv_ns": message.recv_ns,
        "recv_wall_ns": message.recv_wall_ns,
        "exchange_ts_ns": frame.exchange_ts_ns,
        "frame_type": "snapshot" if isinstance(frame, BookSnapshot) else "update",
        "checksum": frame.checksum,
    }
    rows = list(_rows_for_side("bid", frame.bids, base))
    rows.extend(_rows_for_side("ask", frame.asks, base))
    return rows


def iter_tick_rows(
    path: Path | str,
    *,
    limit: int | None = None,
    strict: bool = False,
) -> Iterator[dict[str, Any]]:
    """Stream tick rows from a recording.

    Non-book frames (heartbeat, status, acks) are skipped: they carry no price
    levels, and the raw JSONL remains the record of them.
    """
    for message in iter_raw_messages(path, limit=limit, strict=strict):
        parsed = parse(message.payload)
        if isinstance(parsed, BookSnapshot | BookUpdate):
            yield from tick_rows_from_frame(message, parsed)
