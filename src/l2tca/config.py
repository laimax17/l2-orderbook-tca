"""Static configuration for the phase-one scope: one exchange, one symbol.

Everything here is a plain dataclass so it can be constructed in tests without
touching the environment or the filesystem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Kraken's public WebSocket v2 endpoint. No credentials are ever used: this
#: project only ever consumes public market data.
KRAKEN_WS_URL = "wss://ws.kraken.com/v2"

#: Kraken v1 spelled Bitcoin ``XBT``; the v2 API uses the ISO-ish ``BTC``.
#: Accept both on the CLI and normalise to the v2 spelling on the wire.
SYMBOL_ALIASES = {
    "XBT/USD": "BTC/USD",
    "XBT/EUR": "BTC/EUR",
}

#: Depths the Kraken book channel accepts. Anything else is rejected server-side.
VALID_DEPTHS = (10, 25, 100, 500, 1000)


def normalize_symbol(symbol: str) -> str:
    """Map a user-supplied pair onto the spelling Kraken v2 expects."""
    upper = symbol.upper()
    return SYMBOL_ALIASES.get(upper, upper)


def symbol_to_path_token(symbol: str) -> str:
    """Turn ``BTC/USD`` into ``BTC-USD`` so it is safe in file and partition names."""
    return normalize_symbol(symbol).replace("/", "-")


@dataclass(frozen=True, slots=True)
class FeedConfig:
    """Connection and subscription parameters for a single book stream."""

    symbol: str = "BTC/USD"
    depth: int = 100
    url: str = KRAKEN_WS_URL

    #: Also subscribe to the ``trade`` channel. Off by default so existing
    #: captures and their sequence numbering stay reproducible: turning it on
    #: interleaves a second stream into the recording, which is the point, but
    #: it means a capture recorded with it cannot be compared frame-for-frame
    #: against one recorded without.
    trades: bool = False

    # Connection hygiene. ``ping_interval``/``ping_timeout`` drive the WebSocket
    # protocol-level keepalive; ``stale_after_s`` is our own application-level
    # watchdog: Kraken emits a ``heartbeat`` on every subscribed connection at
    # least once a second, so silence for longer than this means the connection
    # is dead even if TCP has not noticed yet.
    ping_interval_s: float = 20.0
    ping_timeout_s: float = 10.0
    stale_after_s: float = 10.0
    open_timeout_s: float = 15.0
    close_timeout_s: float = 5.0

    # Exponential backoff with full jitter, capped.
    backoff_initial_s: float = 0.5
    backoff_max_s: float = 30.0
    backoff_multiplier: float = 2.0
    backoff_jitter: bool = True
    max_reconnects: int | None = None  # None == retry forever

    def __post_init__(self) -> None:
        if self.depth not in VALID_DEPTHS:
            raise ValueError(f"depth must be one of {VALID_DEPTHS}, got {self.depth}")
        if self.backoff_initial_s <= 0 or self.backoff_max_s < self.backoff_initial_s:
            raise ValueError("invalid backoff bounds")
        if self.backoff_multiplier <= 1.0:
            raise ValueError("backoff_multiplier must be > 1")

    @property
    def wire_symbol(self) -> str:
        return normalize_symbol(self.symbol)

    @property
    def channels(self) -> tuple[str, ...]:
        """Channels to subscribe to, in send order. One ``subscribe`` frame each."""
        return ("book", "trade") if self.trades else ("book",)


@dataclass(frozen=True, slots=True)
class Paths:
    """Filesystem layout. Relative to the repository root unless overridden."""

    root: Path = field(default_factory=lambda: Path(os.environ.get("L2TCA_DATA_ROOT", "data")))

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def parquet(self) -> Path:
        return self.root / "parquet"
