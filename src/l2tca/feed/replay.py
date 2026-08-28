"""Deterministic replay of a recorded session.

Replay is the reason the recorder exists. Given the same file and the same
arguments, :class:`ReplaySource` yields exactly the same frames, in the same
order, with the same timestamps -- so a book bug reproduces on demand and a
signal change can be diffed against a fixed input.

Pacing
------
``speed`` scales the *recorded* inter-arrival gaps, taken from the monotonic
``recv_ns`` stamps:

* ``1.0`` -- wall-clock faithful, for soak tests.
* ``10.0`` -- ten times faster; a ten-minute capture in one minute.
* ``0`` or ``math.inf`` -- no sleeping at all. The default for tests and
  benchmarks: it makes replay a pure function of the file.

``restamp=False`` (default) keeps the recorded clocks, so downstream results
depend only on the file. ``restamp=True`` re-stamps at yield time; use it only
when measuring the live path, never when comparing runs.
"""

from __future__ import annotations

import gzip
import json
import math
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import IO, Any

from l2tca.feed.messages import RawMessage
from l2tca.feed.records import RecordingFormatError, RecordingHeader, ReplayRecord, decode
from l2tca.feed.source import ControlEvent

__all__ = [
    "ReplaySource",
    "iter_raw_messages",
    "iter_records",
    "open_recording",
    "read_header",
]


def open_recording(path: Path | str) -> IO[str]:
    """Open a ``.jsonl`` or ``.jsonl.gz`` recording for reading."""
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def read_header(path: Path | str) -> RecordingHeader | None:
    """Read just the header line, without decoding the body."""
    with open_recording(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            return RecordingHeader.from_dict(obj) if obj.get("kind") == "header" else None
    return None


def iter_records(path: Path | str, *, strict: bool = False) -> Iterator[ReplayRecord]:
    """Decode a recording line by line.

    Args:
        strict: When ``True``, a line that does not decode raises. When ``False``
            (default) it is skipped -- a capture truncated by a hard kill ends in
            a partial line, and losing the last frame beats losing the file.
    """
    with open_recording(path) as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = decode(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if strict:
                    raise RecordingFormatError(f"{path}:{lineno}: {exc}") from exc
                continue
            if record is not None:
                yield record


def iter_raw_messages(
    path: Path | str, *, limit: int | None = None, strict: bool = False
) -> Iterator[RawMessage]:
    """Yield only the market-data frames, as fast as the disk allows.

    The synchronous workhorse used by the Parquet converter and the benchmark
    harness, where pacing would only add noise.
    """
    emitted = 0
    for record in iter_records(path, strict=strict):
        if record.message is None:
            continue
        yield record.message
        emitted += 1
        if limit is not None and emitted >= limit:
            return


class ReplaySource:
    """A :class:`~l2tca.feed.source.MessageSource` backed by a recording."""

    def __init__(
        self,
        path: Path | str,
        *,
        speed: float = 0.0,
        limit: int | None = None,
        restamp: bool = False,
        strict: bool = False,
        sleep: Callable[[float], Any] | None = None,
        on_control: Callable[[ControlEvent], None] | None = None,
    ) -> None:
        if speed < 0:
            raise ValueError("speed must be >= 0 (0 or inf means as fast as possible)")
        self.path = Path(path)
        self.speed = speed
        self.limit = limit
        self.restamp = restamp
        self.strict = strict
        self._sleep = sleep
        self._on_control = on_control
        self._closed = False
        self.header: RecordingHeader | None = None
        self.emitted = 0

    @property
    def paced(self) -> bool:
        return self.speed > 0 and math.isfinite(self.speed)

    async def aclose(self) -> None:
        self._closed = True

    async def __aenter__(self) -> ReplaySource:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def stream(self) -> AsyncIterator[RawMessage]:
        import asyncio

        sleep = self._sleep or asyncio.sleep
        previous_recv_ns: int | None = None

        for record in iter_records(self.path, strict=self.strict):
            if self._closed:
                return
            if record.header is not None:
                self.header = record.header
                continue
            if record.control is not None:
                if self._on_control is not None:
                    self._on_control(record.control)
                continue

            message = record.message
            assert message is not None

            if self.paced and previous_recv_ns is not None:
                gap_s = (message.recv_ns - previous_recv_ns) / 1e9 / self.speed
                # A capture spanning a reconnect can hold a large gap, and a
                # non-monotonic one if the process restarted; clamp both ends.
                if gap_s > 0:
                    await sleep(gap_s)
            previous_recv_ns = message.recv_ns

            if self.restamp:
                message = RawMessage(
                    seq=message.seq,
                    recv_ns=time.perf_counter_ns(),
                    recv_wall_ns=time.time_ns(),
                    payload=message.payload,
                )

            yield message
            self.emitted += 1
            if self.limit is not None and self.emitted >= self.limit:
                return
