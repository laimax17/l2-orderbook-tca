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
    "subscribe_requests",
]


class SubscriptionError(RuntimeError):
    """Kraken rejected the subscription request. Not retryable by itself."""


class StaleConnectionError(ConnectionError):
    """No frame arrived within the staleness budget, and a ping did not revive it."""


def subscribe_request(config: FeedConfig, req_id: int, channel: str = "book") -> dict:
    """The exact ``subscribe`` payload, exposed so tests can assert on it.

    ``depth`` is a book-channel parameter; sending it on a ``trade``
    subscription is not merely redundant, Kraken rejects unknown parameters.
    """
    params: dict = {"channel": channel, "symbol": [config.wire_symbol], "snapshot": True}
    if channel == "book":
        params["depth"] = config.depth
    return {"method": "subscribe", "req_id": req_id, "params": params}


def subscribe_requests(config: FeedConfig, first_req_id: int) -> list[dict]:
    """One request per configured channel, with consecutive ``req_id``s.

    Consecutive rather than shared, because an acknowledgement carries the
    ``req_id`` it answers and nothing else: with one id for both, a rejection
    could not be attributed to the channel that caused it.
    """
    return [
        subscribe_request(config, first_req_id + i, channel)
        for i, channel in enumerate(config.channels)
    ]


def ping_request(req_id: int) -> dict:
    return {"method": "ping", "req_id": req_id}


async def handshake(
    conn: WebSocketLike,
    config: FeedConfig,
    first_req_id: int,
    stamp,
) -> list[RawMessage]:
    """Subscribe to every configured channel and wait for all acknowledgements.

    Frames that arrive while waiting (status, heartbeat, even the snapshot
    itself) are collected and returned so they land in the recording in arrival
    order. Dropping them would leave a hole at the start of every capture, which
    is exactly where the snapshot lives.

    Every subscription must be accounted for before this returns. Returning as
    soon as the book is flowing would let a rejected ``trade`` subscription pass
    unnoticed, and the failure mode of that is the worst kind: a capture that
    looks healthy and silently contains no trades.

    Args:
        first_req_id: Id of the first request; subsequent channels take the
            following ids.
        stamp: Callable turning a raw payload into a :class:`RawMessage`. Passed
            in so the client keeps ownership of sequence numbering and stats.

    Raises:
        SubscriptionError: Kraken answered ``success: false`` for any channel.
        StaleConnectionError: Not every acknowledgement arrived within the budget.
    """
    requests = subscribe_requests(config, first_req_id)
    pending = {request["req_id"]: request["params"]["channel"] for request in requests}
    book_req_id = next((rid for rid, ch in pending.items() if ch == "book"), None)

    for request in requests:
        await conn.send(json.dumps(request))

    collected: list[RawMessage] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(config.stale_after_s, 1.0) * 3

    while pending:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise StaleConnectionError(
                f"no acknowledgement for {sorted(pending.values())} within budget"
            )

        message = stamp(await asyncio.wait_for(conn.recv(), timeout=remaining))
        collected.append(message)

        parsed = parse(message.payload)
        if isinstance(parsed, SubscriptionAck) and parsed.req_id in pending:
            channel = pending.pop(parsed.req_id)
            if not parsed.success:
                raise SubscriptionError(
                    f"kraken rejected the {channel} subscription for "
                    f"{config.wire_symbol} depth={config.depth}: {parsed.error}"
                )
        elif isinstance(parsed, BookSnapshot) and book_req_id in pending:
            # Some deployments deliver the snapshot before the ack; once the book
            # is flowing that subscription is plainly live. Only that one.
            pending.pop(book_req_id)

    return collected
