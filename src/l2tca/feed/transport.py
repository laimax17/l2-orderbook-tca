"""Actually opening a socket, and deciding which failures mean "retry".

Isolated from the reconnect loop so that loop never imports ``websockets`` on a
test path: the client takes its connect factory as an argument, and this module
supplies the only implementation that touches the network.
"""

from __future__ import annotations

import asyncio

from l2tca.config import FeedConfig
from l2tca.feed.source import WebSocketLike
from l2tca.feed.subscription import StaleConnectionError

__all__ = ["default_connect", "transient_errors"]


async def default_connect(config: FeedConfig) -> WebSocketLike:
    """Open a real WebSocket connection to Kraken."""
    from websockets.asyncio.client import connect

    return await connect(
        config.url,
        ping_interval=config.ping_interval_s,
        ping_timeout=config.ping_timeout_s,
        open_timeout=config.open_timeout_s,
        close_timeout=config.close_timeout_s,
        # depth=100 is bursty. A generous queue keeps a slow consumer from making
        # the library drop the connection; the recorder should keep it near empty.
        max_queue=4096,
    )


def transient_errors() -> tuple[type[BaseException], ...]:
    """Exception types that mean "reconnect", resolved lazily so tests need no socket.

    Note that ``asyncio.TimeoutError`` is ``TimeoutError`` on 3.11+, which is an
    ``OSError`` subclass, and ``StaleConnectionError`` is a ``ConnectionError``.
    Both are therefore already covered by ``OSError``; they are listed for the
    reader, not for the interpreter.
    """
    errors: list[type[BaseException]] = [OSError, asyncio.TimeoutError, StaleConnectionError]
    try:
        from websockets.exceptions import WebSocketException

        errors.append(WebSocketException)
    except ImportError:  # pragma: no cover - websockets is a hard dependency
        pass
    return tuple(errors)
