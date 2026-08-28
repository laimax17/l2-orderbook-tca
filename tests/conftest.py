"""Shared fixtures and test doubles."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tests.factories import FIXTURE_CAPTURE, synthetic_capture

from l2tca.config import FeedConfig

# -- captures --------------------------------------------------------------


@pytest.fixture
def config() -> FeedConfig:
    return FeedConfig(symbol="BTC/USD", depth=100)


@pytest.fixture
def capture(tmp_path: Path) -> Path:
    """A small deterministic capture written through the real recorder."""
    return synthetic_capture(tmp_path / "capture.jsonl")


@pytest.fixture(scope="session")
def sample_capture() -> Path:
    """A real recorded session, if one has been committed.

    Skips otherwise. Drop a capture at ``tests/fixtures/sample.jsonl``
    (``l2tca record --duration 600``, trimmed) to activate these.
    """
    if not FIXTURE_CAPTURE.exists():
        pytest.skip(
            "no recorded sample at tests/fixtures/sample.jsonl -- "
            "run `l2tca record` and commit a trimmed capture"
        )
    return FIXTURE_CAPTURE


# -- wire fixtures ---------------------------------------------------------

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


# -- transport double ------------------------------------------------------


class FakeWebSocket:
    """Scripted stand-in for a Kraken connection.

    ``frames`` are returned in order; an exception instance is raised instead.
    Once exhausted the socket either drops (the default, exercising reconnect)
    or hangs (exercising the staleness watchdog).
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
        # A ping is deliberately left unanswered: an unanswered ping is what the
        # staleness watchdog has to escalate on.

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
    """Connect factory handing out ``sockets`` in order, then refusing."""
    remaining = list(sockets)

    async def connect(_config: FeedConfig) -> FakeWebSocket:
        if not remaining:
            raise ConnectionRefusedError("no more scripted sockets")
        return remaining.pop(0)

    return connect


def recording_sleep() -> tuple[list[float], object]:
    """A sleep that records its arguments instead of waiting."""
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        slept.append(delay)

    return slept, sleep
