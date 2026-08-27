"""Kraken public WebSocket v2 client for the ``book`` channel.

Responsibilities, and nothing more: stay connected, stay subscribed, notice when
the connection has gone quiet, and hand every inbound frame upward with both
clocks attached. It does not parse the book, does not maintain state derived
from the book, and never sends anything but ``subscribe``/``unsubscribe``/``ping``.
This project reads public market data only -- there is no authenticated path
here and no order entry anywhere in the codebase.

Testability
-----------
The socket is injected (``connect=``), as are the clock-advancing primitives
(``sleep=``) and the jitter source (``rng=``). The whole reconnect state machine
is therefore exercised in unit tests with an in-memory fake and no real time
spent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from l2tca.config import FeedConfig
from l2tca.feed.backoff import backoff_delays
from l2tca.feed.messages import BookSnapshot, RawMessage, SubscriptionAck, parse
from l2tca.feed.source import ControlEvent

__all__ = [
    "FeedStats",
    "KrakenFeedClient",
    "StaleConnectionError",
    "SubscriptionError",
    "WebSocketLike",
]


class SubscriptionError(RuntimeError):
    """Kraken rejected the subscription request. Not retryable by itself."""


class StaleConnectionError(ConnectionError):
    """No frame arrived within the staleness budget, and a ping did not revive it."""


class WebSocketLike(Protocol):
    """The slice of a WebSocket connection this client actually uses."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


ConnectFactory = Callable[[FeedConfig], Awaitable[WebSocketLike]]


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


async def _default_connect(config: FeedConfig) -> WebSocketLike:
    from websockets.asyncio.client import connect

    return await connect(
        config.url,
        ping_interval=config.ping_interval_s,
        ping_timeout=config.ping_timeout_s,
        open_timeout=config.open_timeout_s,
        close_timeout=config.close_timeout_s,
        # The book channel at depth 100 is bursty. A generous queue keeps a slow
        # consumer from making the library drop the connection, at the cost of
        # memory; the recorder is fast enough that this should stay near empty.
        max_queue=4096,
    )


def _transient_errors() -> tuple[type[BaseException], ...]:
    """Exception types that mean "retry", resolved lazily so tests need no socket."""
    errors: list[type[BaseException]] = [OSError, asyncio.TimeoutError, StaleConnectionError]
    try:
        from websockets.exceptions import WebSocketException

        errors.append(WebSocketException)
    except ImportError:  # pragma: no cover - websockets is a hard dependency
        pass
    return tuple(errors)


class KrakenFeedClient:
    """Maintains one subscribed connection and yields :class:`RawMessage` frames."""

    def __init__(
        self,
        config: FeedConfig,
        *,
        connect: ConnectFactory | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
        on_control: Callable[[ControlEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.stats = FeedStats()
        self._connect_factory = connect or _default_connect
        self._sleep = sleep or asyncio.sleep
        self._rng = rng
        self._on_control = on_control
        self._conn: WebSocketLike | None = None
        self._closed = False
        self._seq = 0
        self._req_id = 0

    # -- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        """Ask the stream to stop and drop the socket. Idempotent."""
        self._closed = True
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()

    async def __aenter__(self) -> KrakenFeedClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # -- main loop ---------------------------------------------------------

    async def stream(self) -> AsyncIterator[RawMessage]:
        """Yield frames indefinitely, reconnecting and resubscribing as needed.

        Close it deterministically -- ``async with contextlib.aclosing(...)`` or
        an explicit :meth:`aclose` -- rather than leaving it to the garbage
        collector, which cannot await the socket shutdown.
        """
        transient = _transient_errors()
        delays = self._new_backoff()
        attempt = 0

        while not self._closed:
            healthy = False
            try:
                self._conn = await self._connect_factory(self.config)
                self.stats.connects += 1
                self._emit("connected", detail=self.config.url)

                for message in await self._handshake(self._conn):
                    yield message
                self._emit("resubscribed" if attempt else "subscribed")

                async for message in self._read_loop(self._conn):
                    healthy = True
                    yield message
            except asyncio.CancelledError:
                raise
            except SubscriptionError:
                # A rejected subscription is a configuration problem (bad symbol,
                # bad depth). Retrying it just spins, so surface it.
                await self._drop()
                raise
            except transient as exc:
                self._emit("disconnected", attempt=attempt, detail=f"{type(exc).__name__}: {exc}")
            finally:
                await self._drop()

            if self._closed:
                break

            # A connection that delivered real data earns a fresh backoff ladder;
            # otherwise a feed that flaps once an hour would creep up to the cap
            # and stay there.
            if healthy:
                delays = self._new_backoff()
                attempt = 0

            attempt += 1
            if self.config.max_reconnects is not None and attempt > self.config.max_reconnects:
                self._emit("giving_up", attempt=attempt)
                break

            delay = next(delays)
            self.stats.reconnects += 1
            self._emit("reconnect", attempt=attempt, detail=f"sleeping {delay:.3f}s")
            await self._sleep(delay)

    # -- internals ---------------------------------------------------------

    def _new_backoff(self):
        return backoff_delays(
            initial=self.config.backoff_initial_s,
            maximum=self.config.backoff_max_s,
            multiplier=self.config.backoff_multiplier,
            jitter=self.config.backoff_jitter,
            rng=self._rng,
        )

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()

    def _emit(self, event: str, *, attempt: int = 0, detail: str = "") -> None:
        import time

        ev = ControlEvent(
            event=event,
            recv_ns=time.perf_counter_ns(),
            recv_wall_ns=time.time_ns(),
            attempt=attempt,
            detail=detail,
        )
        self.stats.control.append(ev)
        if self._on_control is not None:
            self._on_control(ev)

    def subscribe_request(self) -> dict[str, Any]:
        """The exact ``subscribe`` payload, exposed so tests can assert on it."""
        self._req_id += 1
        return {
            "method": "subscribe",
            "req_id": self._req_id,
            "params": {
                "channel": "book",
                "symbol": [self.config.wire_symbol],
                "depth": self.config.depth,
                "snapshot": True,
            },
        }

    async def _handshake(self, conn: WebSocketLike) -> list[RawMessage]:
        """Subscribe and wait for the acknowledgement.

        Frames that arrive while waiting (status, heartbeat, even the snapshot
        itself) are collected and returned so they land in the recording in
        arrival order. Dropping them would leave a hole at the start of every
        capture, which is exactly where the snapshot lives.
        """
        request = self.subscribe_request()
        await conn.send(json.dumps(request))

        collected: list[RawMessage] = []
        deadline_budget = max(self.config.stale_after_s, 1.0) * 3
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_budget

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise StaleConnectionError("no subscription acknowledgement within budget")
            payload = await asyncio.wait_for(conn.recv(), timeout=remaining)
            message = self._stamp(payload)
            collected.append(message)

            parsed = parse(message.payload)
            if isinstance(parsed, SubscriptionAck) and parsed.req_id == request["req_id"]:
                if not parsed.success:
                    raise SubscriptionError(
                        f"kraken rejected subscription for {self.config.wire_symbol} "
                        f"depth={self.config.depth}: {parsed.error}"
                    )
                return collected
            if isinstance(parsed, BookSnapshot):
                # Some deployments deliver the snapshot before the ack; once the
                # book has started flowing the subscription is plainly live.
                return collected

    async def _read_loop(self, conn: WebSocketLike) -> AsyncIterator[RawMessage]:
        """Read frames, applying the staleness watchdog.

        Kraken emits a ``heartbeat`` at least once a second on any subscribed
        connection, so silence is diagnostic. The first silent window buys one
        application-level ``ping`` -- a half-open TCP connection looks identical
        to an idle one until you write to it -- and only a second silent window
        declares the connection dead.
        """
        pinged = False
        while not self._closed:
            try:
                payload = await asyncio.wait_for(conn.recv(), timeout=self.config.stale_after_s)
            except TimeoutError:
                self.stats.stale_timeouts += 1
                if pinged:
                    raise StaleConnectionError(
                        f"no frame for {self.config.stale_after_s * 2:.1f}s across a ping"
                    ) from None
                pinged = True
                self._req_id += 1
                await conn.send(json.dumps({"method": "ping", "req_id": self._req_id}))
                continue

            pinged = False
            yield self._stamp(payload)

    def _stamp(self, payload: str | bytes) -> RawMessage:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        message = RawMessage.stamp(text, self._seq)
        self._seq += 1
        self.stats.messages += 1
        self.stats.bytes_in += len(text)
        self.stats.last_recv_ns = message.recv_ns
        return message
