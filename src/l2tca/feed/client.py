"""Kraken public WebSocket v2 client for the ``book`` channel.

Responsibilities and nothing more: stay connected, stay subscribed, notice when
the connection has gone quiet, and hand every inbound frame upward with both
clocks attached. It does not parse the book and never sends anything but
``subscribe`` / ``unsubscribe`` / ``ping``. This project reads public market data
only: no authenticated path here, no order entry anywhere in the codebase.

The socket is injected (``connect=``), as are the clock-advancing primitive
(``sleep=``) and the jitter source (``rng=``), so the whole reconnect state
machine is exercised in unit tests with an in-memory fake and no real time
spent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from l2tca.config import FeedConfig
from l2tca.feed.backoff import backoff_delays
from l2tca.feed.messages import RawMessage
from l2tca.feed.source import ControlEvent, FeedStats, WebSocketLike
from l2tca.feed.subscription import (
    StaleConnectionError,
    SubscriptionError,
    handshake,
    ping_request,
)
from l2tca.feed.transport import default_connect, transient_errors
from l2tca.logging import get_logger

__all__ = ["KrakenFeedClient"]

log = get_logger(__name__)

ConnectFactory = Callable[[FeedConfig], Awaitable[WebSocketLike]]


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
        self._connect_factory = connect or default_connect
        self._sleep = sleep or asyncio.sleep
        self._rng = rng
        self._on_control = on_control
        self._conn: WebSocketLike | None = None
        self._closed = False
        self._seq = 0
        self._req_id = 0

    async def aclose(self) -> None:
        """Ask the stream to stop and drop the socket. Idempotent."""
        self._closed = True
        await self._drop()

    async def __aenter__(self) -> KrakenFeedClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def stream(self) -> AsyncIterator[RawMessage]:
        """Yield frames indefinitely, reconnecting and resubscribing as needed.

        Close it deterministically -- ``contextlib.aclosing`` or an explicit
        :meth:`aclose` -- rather than leaving it to the garbage collector, which
        cannot await the socket shutdown.
        """
        transient = transient_errors()
        delays = self._new_backoff()
        attempt = 0

        while not self._closed:
            healthy = False
            try:
                self._conn = await self._connect_factory(self.config)
                self.stats.connects += 1
                self._emit("connected", detail=self.config.url)

                first_req_id = self._req_id + 1
                self._req_id += len(self.config.channels)
                for message in await handshake(
                    self._conn, self.config, first_req_id, self._stamp
                ):
                    yield message
                self._emit("resubscribed" if attempt else "subscribed")

                async for message in self._read_loop(self._conn):
                    healthy = True
                    yield message
            except asyncio.CancelledError:
                raise
            except SubscriptionError:
                # A rejected subscription is a configuration problem (bad symbol,
                # bad depth). Retrying just spins, so surface it.
                await self._drop()
                raise
            except transient as exc:
                self._emit("disconnected", attempt=attempt, detail=f"{type(exc).__name__}: {exc}")
            finally:
                await self._drop()

            if self._closed:
                break

            # A connection that delivered real data earns a fresh ladder;
            # otherwise a feed that flaps once an hour creeps up to the cap and
            # stays there.
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

    async def _read_loop(self, conn: WebSocketLike) -> AsyncIterator[RawMessage]:
        """Read frames, applying the staleness watchdog.

        Kraken emits a ``heartbeat`` at least once a second on any subscribed
        connection, so silence is diagnostic. The first silent window buys one
        application-level ping -- a half-open TCP connection looks identical to
        an idle one until you write to it -- and only a second silent window
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
                await conn.send(json.dumps(ping_request(self._req_id)))
                continue

            pinged = False
            yield self._stamp(payload)

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
        ev = ControlEvent(
            event=event,
            recv_ns=time.perf_counter_ns(),
            recv_wall_ns=time.time_ns(),
            attempt=attempt,
            detail=detail,
        )
        self.stats.control.append(ev)
        log.info(ev.event, extra={"attempt": ev.attempt, "detail": ev.detail})
        if self._on_control is not None:
            self._on_control(ev)

    def _stamp(self, payload: str | bytes) -> RawMessage:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        message = RawMessage.stamp(text, self._seq)
        self._seq += 1
        self.stats.messages += 1
        self.stats.bytes_in += len(text)
        self.stats.last_recv_ns = message.recv_ns
        return message
