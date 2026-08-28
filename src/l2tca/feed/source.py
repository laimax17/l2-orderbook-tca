"""The one interface the rest of the system consumes market data through.

Everything downstream -- book, signals, TCA, benchmarks -- reads a
:class:`MessageSource`. Live capture and file replay both satisfy it, so no code
below the feed layer can tell which one it is running against. That is what
makes offline development deterministic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from l2tca.feed.messages import RawMessage

__all__ = ["ControlEvent", "FeedStats", "MessageSource", "WebSocketLike"]


@dataclass(frozen=True, slots=True)
class ControlEvent:
    """A local lifecycle event, not something the exchange sent.

    Recorded alongside the raw frames so a recording shows its own gaps: a
    replay that silently skipped a two-second reconnect would look like a clean
    session and quietly invalidate any latency or staleness analysis run on it.
    """

    event: str  # connected | subscribed | resubscribed | disconnected | reconnect | giving_up
    recv_ns: int
    recv_wall_ns: int
    attempt: int = 0
    detail: str = ""


@dataclass(slots=True)
class FeedStats:
    """Counters worth looking at when a capture behaves oddly."""

    messages: int = 0
    bytes_in: int = 0
    connects: int = 0
    reconnects: int = 0
    stale_timeouts: int = 0
    last_recv_ns: int | None = None
    control: list[ControlEvent] = field(default_factory=list)


class WebSocketLike(Protocol):
    """The slice of a WebSocket connection the client actually uses."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


@runtime_checkable
class MessageSource(Protocol):
    """An async iterable of raw frames that can be shut down cleanly."""

    def stream(self) -> AsyncIterator[RawMessage]:
        """Yield frames until the source is exhausted or closed."""
        ...

    async def aclose(self) -> None:
        """Stop the source and release its resources. Safe to call twice."""
        ...
