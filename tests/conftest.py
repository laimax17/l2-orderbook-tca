"""Shared fixtures and test doubles."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from l2tca.config import FeedConfig
from l2tca.feed.messages import RawMessage
from l2tca.feed.recorder import JsonlRecorder
from l2tca.feed.synthetic import synthetic_session


@pytest.fixture
def config() -> FeedConfig:
    return FeedConfig(symbol="BTC/USD", depth=100)


@pytest.fixture
def capture(tmp_path: Path, config: FeedConfig) -> Path:
    """A small deterministic capture written through the real recorder."""
    path = tmp_path / "capture.jsonl"
    with JsonlRecorder(path, config) as recorder:
        for seq, (offset, payload) in enumerate(
            synthetic_session(symbol=config.wire_symbol, depth=20, updates=120, seed=1)
        ):
            delta = int(offset * 1e9)
            recorder.write_message(
                RawMessage(
                    seq=seq,
                    recv_ns=1_000_000_000 + delta,
                    recv_wall_ns=1_767_000_000_000_000_000 + delta,
                    payload=payload,
                )
            )
    return path


SNAPSHOT_FRAME = json.dumps(
    {
        "channel": "book",
        "type": "snapshot",
        "data": [
            {
                "symbol": "BTC/USD",
                "bids": [{"price": 45283.5, "qty": 0.1}, {"price": 45283.4, "qty": 1.5}],
                "asks": [{"price": 45284.4, "qty": 0.2}, {"price": 45284.5, "qty": 2.0}],
                "checksum": 3070994277,
            }
        ],
    }
)

UPDATE_FRAME = json.dumps(
    {
        "channel": "book",
        "type": "update",
        "data": [
            {
                "symbol": "BTC/USD",
                "bids": [{"price": 45283.5, "qty": 0.0}],
                "asks": [{"price": 45284.4, "qty": 0.5}],
                "checksum": 1301852613,
                "timestamp": "2026-01-02T09:00:01.440295Z",
            }
        ],
    }
)

HEARTBEAT_FRAME = json.dumps({"channel": "heartbeat"})

STATUS_FRAME = json.dumps(
    {
        "channel": "status",
        "type": "update",
        "data": [
            {"api_version": "v2", "connection_id": 12345, "system": "online", "version": "2.0.0"}
        ],
    }
)


class FakeWebSocket:
    """Scripted stand-in for a Kraken connection.

    ``frames`` are returned in order; an exception instance in the list is
    raised instead. Once exhausted the socket either drops (the default, which
    exercises reconnect) or hangs (which exercises the staleness watchdog).
    """

    def __init__(self, frames: list, *, on_exhaust: str = "drop", ack: bool = True) -> None:
        self.pending = list(frames)
        self.sent: list[dict] = []
        self.closed = False
        self.on_exhaust = on_exhaust
        self.ack = ack

    async def send(self, message: str) -> None:
        obj = json.loads(message)
        self.sent.append(obj)
        if self.ack and obj.get("method") == "subscribe":
            self.pending.insert(
                0,
                json.dumps(
                    {
                        "method": "subscribe",
                        "req_id": obj["req_id"],
                        "result": obj["params"],
                        "success": True,
                    }
                ),
            )
        elif obj.get("method") == "ping":
            # Deliberately silent: a ping that goes unanswered is what the
            # staleness watchdog has to escalate on.
            pass

    async def recv(self) -> str:
        if self.pending:
            item = self.pending.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if self.on_exhaust == "drop":
            raise ConnectionResetError("simulated drop")
        await asyncio.Event().wait()  # pragma: no cover - cancelled by the test
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


def sequenced_connect(sockets: list[FakeWebSocket]):
    """Connect factory handing out ``sockets`` in order, then raising."""
    remaining = list(sockets)

    async def connect(_config: FeedConfig) -> FakeWebSocket:
        if not remaining:
            raise ConnectionRefusedError("no more scripted sockets")
        return remaining.pop(0)

    return connect
