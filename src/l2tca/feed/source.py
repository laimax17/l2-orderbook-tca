"""The one interface the rest of the system consumes market data through.

Everything downstream -- book, signals, TCA, benchmarks -- reads a
:class:`MessageSource`. Live capture and file replay both satisfy it, so no code
below the feed layer can tell which one it is running against. That is what
makes offline development deterministic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from l2tca.feed.messages import RawMessage

__all__ = ["ControlEvent", "MessageSource"]


@dataclass(frozen=True, slots=True)
class ControlEvent:
    """A local lifecycle event, not something the exchange sent.

    Recorded alongside the raw frames so a recording shows its own gaps: a
    replay that silently skipped a two-second reconnect would look like a clean
    session and quietly invalidate any latency or staleness analysis run on it.
    """

    event: str  # "connected" | "disconnected" | "reconnect" | "resubscribed"
    recv_ns: int
    recv_wall_ns: int
    attempt: int = 0
    detail: str = ""


@runtime_checkable
class MessageSource(Protocol):
    """An async iterable of raw frames that can be shut down cleanly."""

    def stream(self) -> AsyncIterator[RawMessage]:
        """Yield frames until the source is exhausted or closed."""
        ...

    async def aclose(self) -> None:
        """Stop the source and release its resources. Safe to call twice."""
        ...
