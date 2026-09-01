"""Derive the ``snapshot`` and ``signal`` tables by replaying a capture.

The missing producer. ``io/schema.py`` has defined both tables since the start
and ``plot/`` reads them, but nothing wrote them: they are the first artefacts
that need a working order book, so they could not exist until it did.

Unlike :mod:`l2tca.io.convert`, which flattens frames without interpreting them,
everything here goes through :class:`~l2tca.book.sequence.SequenceTracker`. That
means each row carries the checksum verdict for the frame that produced it, and
a row whose ``checksum_ok`` is false is a row derived from a book the exchange
disagreed with. Keeping it rather than dropping it is deliberate -- silently
skipping frames would make a gap in the output look like a quiet market.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from l2tca.book.sequence import SequenceTracker
from l2tca.book.types import BookView
from l2tca.config import symbol_to_path_token
from l2tca.feed.messages import BookSnapshot, BookUpdate, RawMessage
from l2tca.feed.parser import parse
from l2tca.feed.replay import iter_raw_messages
from l2tca.io.schema import SCHEMA_VERSION
from l2tca.signals.microstructure import micro_price, order_book_imbalance, quoted_spread

__all__ = [
    "DEFAULT_IMBALANCE_LEVELS",
    "iter_book_views",
    "iter_derived_rows",
    "signal_rows",
    "snapshot_row",
]

#: Imbalance is reported at each of these depths. One level is the touch, which
#: is what a taker meets; five is where the queue behind it lives. Both, because
#: which one carries information is the question, not the assumption.
DEFAULT_IMBALANCE_LEVELS = (1, 5)


def iter_book_views(
    path: Path | str,
    *,
    symbol: str = "BTC/USD",
    depth: int = 100,
    levels: int = 10,
    price_precision: int = 1,
    qty_precision: int = 8,
    limit: int | None = None,
) -> Iterator[tuple[RawMessage, BookView]]:
    """Replay ``path`` through the book, yielding a view per applied book frame.

    Updates arriving before the opening snapshot are skipped: there is no book
    to apply them to, and inventing one would put fabricated depth in the table.

    Args:
        levels: Depth carried by each view, and by the ``snapshot`` table's list
            columns. Ten rather than the full hundred because every downstream
            question here is asked at the top of the book, and a hundred-deep
            list column per frame is an order of magnitude more storage for
            depth nothing reads.
    """
    tracker = SequenceTracker(
        symbol, depth, price_precision=price_precision, qty_precision=qty_precision
    )
    ready = False

    for message in iter_raw_messages(path, limit=limit):
        frame = parse(message.payload)
        if isinstance(frame, BookSnapshot):
            tracker.on_snapshot(frame)
            ready = True
            checksum_ok = None
        elif isinstance(frame, BookUpdate):
            if not ready:
                continue
            checksum_ok = tracker.on_update(frame) if frame.checksum is not None else None
        else:
            continue

        yield (
            message,
            tracker.book.view(
                levels,
                recv_ns=message.recv_ns,
                recv_wall_ns=message.recv_wall_ns,
                exchange_ts_ns=frame.exchange_ts_ns,
                checksum_ok=checksum_ok,
            ),
        )


def snapshot_row(view: BookView) -> dict[str, Any]:
    """One ``snapshot`` row: the book at an instant, depth in parallel lists."""
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol_to_path_token(view.symbol),
        "book_seq": view.seq,
        "recv_ns": view.recv_ns,
        "recv_wall_ns": view.recv_wall_ns,
        "exchange_ts_ns": view.exchange_ts_ns,
        "checksum_ok": view.checksum_ok,
        "bid_px": [float(level.price) for level in view.bids],
        "bid_qty": [float(level.qty) for level in view.bids],
        "ask_px": [float(level.price) for level in view.asks],
        "ask_qty": [float(level.qty) for level in view.asks],
    }


def signal_rows(
    view: BookView,
    *,
    imbalance_levels: tuple[int, ...] = DEFAULT_IMBALANCE_LEVELS,
) -> list[dict[str, Any]]:
    """Long-format ``signal`` rows for one view.

    Empty when the view has no two-sided touch: every factor here is defined
    against both sides, and writing a zero would be a value the book never had.
    """
    if not view.bids or not view.asks:
        return []

    base = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol_to_path_token(view.symbol),
        "book_seq": view.seq,
        "recv_ns": view.recv_ns,
        "recv_wall_ns": view.recv_wall_ns,
    }
    mid = (float(view.bids[0].price) + float(view.asks[0].price)) / 2
    rows = [
        {**base, "name": "mid", "value": mid, "levels": 1},
        {**base, "name": "micro_price", "value": micro_price(view), "levels": 1},
        {**base, "name": "quoted_spread_bps", "value": quoted_spread(view), "levels": 1},
    ]
    rows.extend(
        {
            **base,
            "name": "imbalance",
            "value": order_book_imbalance(view, levels=n),
            "levels": n,
        }
        for n in imbalance_levels
        if len(view.bids) >= n and len(view.asks) >= n
    )
    return rows


def iter_derived_rows(
    path: Path | str,
    *,
    imbalance_levels: tuple[int, ...] = DEFAULT_IMBALANCE_LEVELS,
    **kwargs: Any,
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Stream ``(snapshot_row, signal_rows)`` pairs, one pair per applied frame.

    Paired rather than returned as two passes: rebuilding the book is the
    expensive part, and doing it twice to write two tables would double the cost
    of the one step that cannot be made cheaper.
    """
    for _message, view in iter_book_views(path, **kwargs):
        yield snapshot_row(view), signal_rows(view, imbalance_levels=imbalance_levels)
