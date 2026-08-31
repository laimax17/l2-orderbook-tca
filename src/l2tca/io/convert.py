"""Turn a raw JSONL capture into ``tick`` and ``trade`` table rows.

Deliberately independent of :mod:`l2tca.book`: converting a recording to Parquet
must work before a single line of the book is written, so that storage, reading
and validation can all be proven end to end against real captured bytes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from l2tca.config import symbol_to_path_token
from l2tca.feed.messages import BookLevel, BookSnapshot, BookUpdate, RawMessage, Trades
from l2tca.feed.parser import parse
from l2tca.feed.replay import iter_raw_messages
from l2tca.io.schema import SCHEMA_VERSION

__all__ = ["iter_tick_rows", "iter_trade_rows", "tick_rows_from_frame", "trade_rows_from_frame"]


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


def trade_rows_from_frame(message: RawMessage, frame: Trades) -> list[dict[str, Any]]:
    """Flatten one parsed trade frame into one row per print.

    The frame's grouping is not preserved as a column. Prints from one taker
    share ``recv_ns`` and an exchange timestamp, which is enough to regroup them
    on read, and inventing a batch id would put a number in the data that the
    venue never sent.

    ``frame_type`` is preserved, because that one is not recoverable on read: a
    backfilled print is indistinguishable from a live one once its provenance is
    dropped, and it is wrong to treat them alike.
    """
    base = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol_to_path_token(frame.symbol),
        "seq": message.seq,
        "recv_ns": message.recv_ns,
        "recv_wall_ns": message.recv_wall_ns,
        "frame_type": "snapshot" if frame.is_snapshot else "update",
    }
    return [
        {
            **base,
            "exchange_ts_ns": trade.exchange_ts_ns,
            "trade_id": trade.trade_id,
            "side": trade.side,
            "price": float(trade.price),
            "qty": float(trade.qty),
            "ord_type": trade.ord_type,
        }
        for trade in frame.trades
    ]


def iter_trade_rows(
    path: Path | str,
    *,
    limit: int | None = None,
    strict: bool = False,
) -> Iterator[dict[str, Any]]:
    """Stream trade rows from a recording.

    Yields nothing for a capture recorded without ``--trades``, which is not an
    error: most captures in this project are book-only by design.
    """
    for message in iter_raw_messages(path, limit=limit, strict=strict):
        parsed = parse(message.payload)
        if isinstance(parsed, Trades):
            yield from trade_rows_from_frame(message, parsed)
