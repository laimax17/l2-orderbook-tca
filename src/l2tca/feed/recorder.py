"""Lossless capture of a live session to newline-delimited JSON.

This is the highest-value component in the repository. Everything downstream --
book reconstruction, microstructure signals, TCA -- is developed against files
written here, so the core logic can be iterated on offline, deterministically,
against the same bytes every single run. A capture is the unit test corpus.

Record layout (one JSON object per line)
---------------------------------------
``header``
    Written once at open: capture parameters and, importantly, the pairing of
    ``time.perf_counter_ns()`` with ``time.time_ns()``. ``perf_counter_ns`` has
    an arbitrary origin, so without this pairing a recording's monotonic
    timestamps cannot be related to wall clock after the process exits.
``msg``
    One inbound frame. ``payload`` is the exact text Kraken sent -- never
    re-serialised, so replay reproduces the session byte for byte, including
    anything malformed the parser needs to survive.
``control``
    A local lifecycle event (connect, disconnect, reconnect). These make a
    capture's own gaps visible; a replay that silently glossed over a two-second
    reconnect would look like a clean session and invalidate any staleness or
    latency analysis run against it.
"""

from __future__ import annotations

import gzip
import io
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from l2tca.config import FeedConfig, symbol_to_path_token
from l2tca.feed.messages import RawMessage
from l2tca.feed.source import ControlEvent

__all__ = ["RECORDING_FORMAT_VERSION", "JsonlRecorder", "RecordingHeader", "default_capture_path"]

#: Bumped whenever the on-disk record layout changes incompatibly.
RECORDING_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class RecordingHeader:
    """Metadata captured once, at the top of every recording file."""

    v: int
    symbol: str
    depth: int
    url: str
    started_wall_ns: int
    #: ``perf_counter_ns()`` and ``time_ns()`` read back to back at open, so the
    #: monotonic timestamps in this file can be anchored to wall clock later.
    perf_epoch_ns: int
    perf_epoch_wall_ns: int

    @classmethod
    def from_dict(cls, obj: dict) -> RecordingHeader:
        return cls(
            v=int(obj.get("v", 0)),
            symbol=str(obj.get("symbol", "")),
            depth=int(obj.get("depth", 0)),
            url=str(obj.get("url", "")),
            started_wall_ns=int(obj.get("started_wall_ns", 0)),
            perf_epoch_ns=int(obj.get("perf_epoch_ns", 0)),
            perf_epoch_wall_ns=int(obj.get("perf_epoch_wall_ns", 0)),
        )

    def perf_ns_to_wall_ns(self, perf_ns: int) -> int:
        """Anchor a monotonic timestamp from this file onto the wall clock."""
        return self.perf_epoch_wall_ns + (perf_ns - self.perf_epoch_ns)


def default_capture_path(directory: Path, config: FeedConfig, *, compress: bool = False) -> Path:
    """``kraken_book_BTC-USD_d100_20260827T154900Z.jsonl`` under ``directory``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    token = symbol_to_path_token(config.symbol)
    suffix = ".jsonl.gz" if compress else ".jsonl"
    return directory / f"kraken_book_{token}_d{config.depth}_{stamp}{suffix}"


class JsonlRecorder:
    """Append-only JSONL writer. Not thread-safe; drive it from one task."""

    def __init__(
        self,
        path: Path,
        config: FeedConfig,
        *,
        flush_every: int = 200,
        flush_interval_s: float = 2.0,
    ) -> None:
        self.path = Path(path)
        self.config = config
        self.flush_every = max(1, flush_every)
        self.flush_interval_s = flush_interval_s
        self.records = 0
        self.bytes_written = 0
        self._fh: io.TextIOBase | None = None
        self._since_flush = 0
        self._last_flush = 0.0
        self._header: RecordingHeader | None = None

    @property
    def header(self) -> RecordingHeader | None:
        return self._header

    def open(self) -> JsonlRecorder:
        if self._fh is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The handle outlives this call by design -- a recorder is opened once
        # for a session and closed by close()/__exit__.
        if self.path.suffix == ".gz":
            self._fh = gzip.open(  # noqa: SIM115
                self.path, "at", encoding="utf-8", newline="\n"
            )  # type: ignore[assignment]
        else:
            self._fh = self.path.open("a", encoding="utf-8", newline="\n")

        perf_epoch_ns = time.perf_counter_ns()
        perf_epoch_wall_ns = time.time_ns()
        self._header = RecordingHeader(
            v=RECORDING_FORMAT_VERSION,
            symbol=self.config.wire_symbol,
            depth=self.config.depth,
            url=self.config.url,
            started_wall_ns=perf_epoch_wall_ns,
            perf_epoch_ns=perf_epoch_ns,
            perf_epoch_wall_ns=perf_epoch_wall_ns,
        )
        self._write(
            {
                "v": RECORDING_FORMAT_VERSION,
                "kind": "header",
                "symbol": self._header.symbol,
                "depth": self._header.depth,
                "url": self._header.url,
                "started_wall_ns": self._header.started_wall_ns,
                "perf_epoch_ns": self._header.perf_epoch_ns,
                "perf_epoch_wall_ns": self._header.perf_epoch_wall_ns,
            }
        )
        self._last_flush = time.monotonic()
        return self

    def write_message(self, message: RawMessage) -> None:
        self._write(
            {
                "v": RECORDING_FORMAT_VERSION,
                "kind": "msg",
                "seq": message.seq,
                "recv_ns": message.recv_ns,
                "recv_wall_ns": message.recv_wall_ns,
                "payload": message.payload,
            }
        )
        self._maybe_flush()

    def write_control(self, event: ControlEvent) -> None:
        self._write(
            {
                "v": RECORDING_FORMAT_VERSION,
                "kind": "control",
                "event": event.event,
                "recv_ns": event.recv_ns,
                "recv_wall_ns": event.recv_wall_ns,
                "attempt": event.attempt,
                "detail": event.detail,
            }
        )
        # Lifecycle events are rare and are exactly what you want on disk if the
        # process dies, so they always hit the OS immediately.
        self.flush()

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            self._since_flush = 0
            self._last_flush = time.monotonic()

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.flush()
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> JsonlRecorder:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internals ---------------------------------------------------------

    def _write(self, obj: dict) -> None:
        if self._fh is None:
            raise RuntimeError("recorder is not open")
        # separators drop the whitespace json.dumps adds by default: on a
        # 100-level book at a few hundred messages a second it is real bytes.
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self._fh.write(line)
        self._fh.write("\n")
        self.records += 1
        self.bytes_written += len(line) + 1
        self._since_flush += 1

    def _maybe_flush(self) -> None:
        due_by_count = self._since_flush >= self.flush_every
        due_by_time = time.monotonic() - self._last_flush >= self.flush_interval_s
        if due_by_count or due_by_time:
            self.flush()
