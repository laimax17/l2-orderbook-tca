"""Deterministic generator of Kraken-v2-shaped book frames.

What this is for: producing a recording when a live connection is not available
-- a locked-down network, CI, a plane -- so the replay path, the Parquet layer,
the CLI and the benchmark harness can all be exercised end to end.

What this is **not**: a market model. The price process is a lazy random walk
and the depth profile is arbitrary. Nothing produced here should be used to
evaluate a signal or a cost estimate; the numbers would be meaningless. Use a
real capture for anything with a conclusion attached.

One deliberate omission: synthetic frames carry **no** ``checksum`` field.
Fabricating a checksum would require the very CRC32 implementation that
:func:`~l2tca.book.l2_book.kraken_book_checksum` is meant to be, and a
self-consistent fake would validate a wrong implementation against itself. The
checksum path must be tested against a real capture or a hand-derived vector.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

__all__ = ["synthetic_session"]


def _fmt(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}"


def synthetic_session(
    *,
    symbol: str = "BTC/USD",
    depth: int = 100,
    updates: int = 2_000,
    seed: int = 7,
    start_mid: Decimal = Decimal("64000.0"),
    tick: Decimal = Decimal("0.1"),
    price_places: int = 1,
    qty_places: int = 8,
    mean_gap_s: float = 0.05,
    heartbeat_every: int = 40,
) -> Iterator[tuple[float, str]]:
    """Yield ``(offset_seconds, frame_json)`` for one synthetic session.

    The first market-data frame is a full snapshot of ``depth`` levels per side,
    followed by ``updates`` incremental frames touching a handful of levels
    each, interleaved with ``heartbeat`` frames -- the same shape a real
    subscription produces.

    Fully determined by ``seed``: the same seed yields the same bytes.
    """
    rng = random.Random(seed)
    t = 0.0
    base = datetime(2026, 1, 2, 9, 0, 0, tzinfo=UTC)

    def stamp(offset: float) -> str:
        return (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")

    yield (
        t,
        json.dumps(
            {
                "channel": "status",
                "type": "update",
                "data": [
                    {
                        "api_version": "v2",
                        "connection_id": 1,
                        "system": "online",
                        "version": "2.0.0",
                    }
                ],
            }
        ),
    )
    t += 0.01
    yield (
        t,
        json.dumps(
            {
                "method": "subscribe",
                "req_id": 1,
                "result": {"channel": "book", "depth": depth, "snapshot": True, "symbol": symbol},
                "success": True,
                "time_in": stamp(t),
                "time_out": stamp(t),
            }
        ),
    )

    mid = start_mid
    half = tick * Decimal(5)
    bids = [mid - half - tick * i for i in range(depth)]
    asks = [mid + half + tick * i for i in range(depth)]
    qty: dict[Decimal, Decimal] = {}
    for price in bids + asks:
        qty[price] = (Decimal(rng.randrange(1, 5_000)) / Decimal(10_000)).quantize(
            Decimal(1).scaleb(-qty_places)
        )

    t += 0.02
    yield (
        t,
        json.dumps(
            {
                "channel": "book",
                "type": "snapshot",
                "data": [
                    {
                        "symbol": symbol,
                        "bids": [
                            {
                                "price": float(_fmt(p, price_places)),
                                "qty": float(_fmt(qty[p], qty_places)),
                            }
                            for p in bids
                        ],
                        "asks": [
                            {
                                "price": float(_fmt(p, price_places)),
                                "qty": float(_fmt(qty[p], qty_places)),
                            }
                            for p in asks
                        ],
                    }
                ],
            }
        ),
    )

    for i in range(updates):
        t += rng.expovariate(1.0 / mean_gap_s)
        if heartbeat_every and i % heartbeat_every == 0:
            yield (t, json.dumps({"channel": "heartbeat"}))
            t += 0.001

        touched_bids: list[dict[str, float]] = []
        touched_asks: list[dict[str, float]] = []
        for _ in range(rng.randrange(1, 4)):
            side_is_bid = rng.random() < 0.5
            book_side = bids if side_is_bid else asks
            index = min(int(abs(rng.gauss(0, 6))), len(book_side) - 1)
            price = book_side[index]
            # A third of touches are cancellations, which is roughly the shape of
            # a real book channel and, more importantly, exercises the delete path.
            new_qty = (
                Decimal(0)
                if rng.random() < 0.33
                else (Decimal(rng.randrange(1, 5_000)) / Decimal(10_000)).quantize(
                    Decimal(1).scaleb(-qty_places)
                )
            )
            entry = {
                "price": float(_fmt(price, price_places)),
                "qty": float(_fmt(new_qty, qty_places)),
            }
            (touched_bids if side_is_bid else touched_asks).append(entry)

        yield (
            t,
            json.dumps(
                {
                    "channel": "book",
                    "type": "update",
                    "data": [
                        {
                            "symbol": symbol,
                            "bids": touched_bids,
                            "asks": touched_asks,
                            "timestamp": stamp(t),
                        }
                    ],
                }
            ),
        )
