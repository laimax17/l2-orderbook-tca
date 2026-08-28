"""Lossless capture of a live session to newline-delimited JSON.

This is the highest-value component in the repository. Everything downstream --
book reconstruction, microstructure signals, TCA -- is developed against files
written here, so the core logic can be iterated on offline, deterministically,
against the same bytes every run. A capture is the unit-test corpus.

The record layout is in :mod:`l2tca.feed.records`.
"""

from __future__ import annotations

import gzip
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from l2tca.config import FeedConfig, symbol_to_path_token
from l2tca.feed.messages import RawMessage
from l2tca.feed.records import (
    RECORDING_FORMAT_VERSION,
    RecordingHeader,
    encode_control,
    encode_header,
    encode_message,
)
from l2tca.feed.source import ControlEvent
from l2tca.logging import get_logger

__all__ = ["JsonlRecorder", "default_capture_path"]

log = get_logger(__name__)


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
        # The handle outlives this call by design: a recorder is opened once for
        # a session and closed by close()/__exit__.
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
        self._write(encode_header(self._header))
        self._last_flush = time.monotonic()
        log.info("capture_open", extra={"path": str(self.path), "symbol": self._header.symbol})
        return self

    def write_message(self, message: RawMessage) -> None:
        self._write(encode_message(message))
        self._maybe_flush()

    def write_control(self, event: ControlEvent) -> None:
        self._write(encode_control(event))
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
            log.info("capture_close", extra={"path": str(self.path), "records": self.records})

    def __enter__(self) -> JsonlRecorder:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _write(self, obj: dict) -> None:
        if self._fh is None:
            raise RuntimeError("recorder is not open")
        # separators drop the whitespace json.dumps adds by default: on a
        # 100-level book at a few hundred messages a second that is real bytes.
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
