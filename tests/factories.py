"""Test data factories.

Two sources, used together:

*Hand-built frames* -- :func:`snapshot_frame`, :func:`update_frame`,
:func:`book_view` -- for edge cases. A capture will not reliably contain a
delete for a price outside the window, or a one-sided book, at the moment a
test needs one.

*Recorded samples* -- :func:`iter_capture_frames`, :func:`capture_book_frames` --
for shape and volume. A hand-built frame reflects what the author believed the
wire format to be; a capture reflects what it is.

Drop a real capture at ``tests/fixtures/sample.jsonl`` and the sample-backed
helpers pick it up. Without one they fall back to a seeded synthetic session,
so the suite runs anywhere -- but see :mod:`l2tca.feed.synthetic` for why
synthetic frames prove less than they appear to.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from l2tca.book.types import BookView, Level
from l2tca.config import FeedConfig
from l2tca.feed.messages import BookLevel, BookSnapshot, BookUpdate, RawMessage
from l2tca.feed.parser import parse
from l2tca.feed.recorder import JsonlRecorder
from l2tca.feed.replay import iter_raw_messages
from l2tca.feed.synthetic import synthetic_session
from l2tca.tca.types import Fill, Order, Side

__all__ = [
    "FIXTURE_CANDIDATES",
    "FIXTURE_CAPTURE",
    "book_view",
    "capture_book_frames",
    "fill",
    "fixture_capture",
    "iter_capture_frames",
    "levels",
    "order",
    "raw_messages",
    "snapshot_frame",
    "update_frame",
    "write_capture",
]

_FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: A committed capture activates the sample-backed tests. Gzip is accepted and
#: preferred: book JSONL compresses about tenfold, and the recorder and replayer
#: both handle ``.gz`` transparently.
FIXTURE_CANDIDATES = (_FIXTURE_DIR / "sample.jsonl", _FIXTURE_DIR / "sample.jsonl.gz")


def fixture_capture() -> Path | None:
    """The committed sample capture, or ``None`` if none has been added yet."""
    return next((p for p in FIXTURE_CANDIDATES if p.exists()), None)


#: Kept for direct reference in skip messages.
FIXTURE_CAPTURE = FIXTURE_CANDIDATES[0]

PriceQty = tuple[str, str]


def levels(pairs: list[PriceQty]) -> tuple[Level, ...]:
    """``[("100.0", "10")]`` -> a tuple of :class:`Level`, exact decimals."""
    return tuple(Level(Decimal(p), Decimal(q)) for p, q in pairs)


def _wire_levels(pairs: list[PriceQty]) -> tuple[BookLevel, ...]:
    return tuple(BookLevel(Decimal(p), Decimal(q)) for p, q in pairs)


def snapshot_frame(
    bids: list[PriceQty],
    asks: list[PriceQty],
    *,
    symbol: str = "BTC/USD",
    checksum: int | None = None,
    exchange_ts_ns: int | None = None,
) -> BookSnapshot:
    return BookSnapshot(symbol, _wire_levels(bids), _wire_levels(asks), checksum, exchange_ts_ns)


def update_frame(
    bids: list[PriceQty],
    asks: list[PriceQty],
    *,
    symbol: str = "BTC/USD",
    checksum: int | None = None,
    exchange_ts_ns: int | None = None,
) -> BookUpdate:
    return BookUpdate(symbol, _wire_levels(bids), _wire_levels(asks), checksum, exchange_ts_ns)


def book_view(
    bids: list[PriceQty],
    asks: list[PriceQty],
    *,
    symbol: str = "BTC/USD",
    seq: int = 1,
    recv_ns: int = 1_000,
    recv_wall_ns: int = 2_000,
    exchange_ts_ns: int | None = None,
    checksum_ok: bool | None = None,
) -> BookView:
    return BookView(
        symbol=symbol,
        seq=seq,
        recv_ns=recv_ns,
        recv_wall_ns=recv_wall_ns,
        exchange_ts_ns=exchange_ts_ns,
        bids=levels(bids),
        asks=levels(asks),
        checksum_ok=checksum_ok,
    )


def order(
    side: Side = Side.BID,
    qty: str = "10",
    *,
    decision_ns: int = 1_000,
    symbol: str = "BTC/USD",
    order_id: str = "o1",
) -> Order:
    return Order(symbol, side, Decimal(qty), decision_ns, order_id=order_id)


def fill(ts_ns: int, price: str, qty: str, *, fee: str = "0", is_taker: bool = True) -> Fill:
    return Fill(ts_ns, Decimal(price), Decimal(qty), Decimal(fee), is_taker)


def trade_frame(
    prints: list[tuple[str, str, str]],
    *,
    symbol: str = "BTC/USD",
    first_trade_id: int = 1,
    timestamp: str = "2026-08-30T02:24:31.123456Z",
    snapshot: bool = False,
) -> str:
    """A ``trade`` frame carrying ``(side, price, qty)`` prints, as JSON text.

    Built from the wire shape rather than from :class:`Trades` so that the
    parser is exercised rather than bypassed -- these tests exist partly to pin
    a format that could not be verified against a live socket.
    """
    return json.dumps(
        {
            "channel": "trade",
            "type": "snapshot" if snapshot else "update",
            "data": [
                {
                    "symbol": symbol,
                    "side": side,
                    "price": float(price),
                    "qty": float(qty),
                    "ord_type": "market",
                    "trade_id": first_trade_id + i,
                    "timestamp": timestamp,
                }
                for i, (side, price, qty) in enumerate(prints)
            ],
        }
    )


def raw_messages(payloads: list[str], *, step_ns: int = 1_000_000) -> list[RawMessage]:
    """Stamp payloads with a regular, deterministic clock."""
    return [
        RawMessage(seq=i, recv_ns=1_000 + i * step_ns, recv_wall_ns=2_000 + i * step_ns, payload=p)
        for i, p in enumerate(payloads)
    ]


def write_capture(path: Path, messages: list[RawMessage], config: FeedConfig | None = None) -> Path:
    """Write messages through the real recorder, so tests exercise the real format."""
    with JsonlRecorder(path, config or FeedConfig()) as recorder:
        for message in messages:
            recorder.write_message(message)
    return path


def synthetic_capture(path: Path, *, updates: int = 120, depth: int = 20, seed: int = 1) -> Path:
    config = FeedConfig(depth=100)
    messages = [
        RawMessage(
            seq=seq,
            recv_ns=1_000_000_000 + int(offset * 1e9),
            recv_wall_ns=1_767_000_000_000_000_000 + int(offset * 1e9),
            payload=payload,
        )
        for seq, (offset, payload) in enumerate(
            synthetic_session(depth=depth, updates=updates, seed=seed)
        )
    ]
    return write_capture(path, messages, config)


def iter_capture_frames(path: Path, *, limit: int | None = None):
    """Yield parsed frames from a capture, whatever kinds it holds."""
    for message in iter_raw_messages(path, limit=limit):
        yield parse(message.payload)


def capture_book_frames(
    path: Path, *, limit: int | None = None
) -> tuple[list[BookSnapshot], list[BookUpdate]]:
    """Split a capture into its snapshot and update frames."""
    snapshots: list[BookSnapshot] = []
    updates: list[BookUpdate] = []
    for frame in iter_capture_frames(path, limit=limit):
        if isinstance(frame, BookSnapshot):
            snapshots.append(frame)
        elif isinstance(frame, BookUpdate):
            updates.append(frame)
    return snapshots, updates


def payloads_from(path: Path, *, limit: int | None = None) -> list[str]:
    """Raw payload text from a capture, for parser round-trip tests."""
    return [json.loads(json.dumps(m.payload)) for m in iter_raw_messages(path, limit=limit)]
