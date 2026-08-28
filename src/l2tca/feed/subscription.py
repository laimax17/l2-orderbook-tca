"""Subscribe / ping wire messages and the post-connect handshake.

Split from the connection loop because this is the part that is specific to
Kraken's v2 protocol; the loop in :mod:`l2tca.feed.client` is generic
connect-read-reconnect machinery.
"""

from __future__ import annotations

import asyncio
import json

from l2tca.config import FeedConfig
from l2tca.feed.messages import BookSnapshot, RawMessage, SubscriptionAck
from l2tca.feed.parser import parse
from l2tca.feed.source import WebSocketLike

__all__ = [
    "StaleConnectionError",
    "SubscriptionError",
    "handshake",
    "ping_request",
    "subscribe_request",
]


class SubscriptionError(RuntimeError):
    """Kraken rejected the subscription request. Not retryable by itself."""


class StaleConnectionError(ConnectionError):
    """No frame arrived within the staleness budget, and a ping did not revive it."""


def subscribe_request(config: FeedConfig, req_id: int) -> dict:
    """The exact ``subscribe`` payload, exposed so tests can assert on it."""
    return {
        "method": "subscribe",
        "req_id": req_id,
        "params": {
            "channel": "book",
            "symbol": [config.wire_symbol],
            "depth": config.depth,
            "snapshot": True,
        },
    }


def ping_request(req_id: int) -> dict:
    return {"method": "ping", "req_id": req_id}


async def handshake(
    conn: WebSocketLike,
    config: FeedConfig,
    req_id: int,
    stamp,
) -> list[RawMessage]:
    """Subscribe and wait for the acknowledgement.

    Frames that arrive while waiting (status, heartbeat, even the snapshot
    itself) are collected and returned so they land in the recording in arrival
    order. Dropping them would leave a hole at the start of every capture, which
    is exactly where the snapshot lives.

    Args:
        stamp: Callable turning a raw payload into a :class:`RawMessage`. Passed
            in so the client keeps ownership of sequence numbering and stats.

    Raises:
        SubscriptionError: Kraken answered ``success: false``.
        StaleConnectionError: No acknowledgement within the budget.
    """
    request = subscribe_request(config, req_id)
    await conn.send(json.dumps(request))

    collected: list[RawMessage] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(config.stale_after_s, 1.0) * 3

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise StaleConnectionError("no subscription acknowledgement within budget")

        message = stamp(await asyncio.wait_for(conn.recv(), timeout=remaining))
        collected.append(message)

        parsed = parse(message.payload)
        if isinstance(parsed, SubscriptionAck) and parsed.req_id == req_id:
            if not parsed.success:
                raise SubscriptionError(
                    f"kraken rejected subscription for {config.wire_symbol} "
                    f"depth={config.depth}: {parsed.error}"
                )
            return collected
        if isinstance(parsed, BookSnapshot):
            # Some deployments deliver the snapshot before the ack; once the book
            # is flowing the subscription is plainly live.
            return collected
