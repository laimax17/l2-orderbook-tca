"""The on-disk record layout shared by the recorder and the replayer.

One JSON object per line, three kinds:

``header``
    Written once at open: capture parameters and, importantly, the pairing of
    ``time.perf_counter_ns()`` with ``time.time_ns()``. ``perf_counter_ns`` has
    an arbitrary origin, so without this pairing a recording's monotonic
    timestamps cannot be related to wall clock after the process exits.
``msg``
    One inbound frame. ``payload`` is the exact text Kraken sent -- never
    re-serialised, so replay reproduces the session byte for byte, including
    anything malformed the parser has to survive.
``control``
    A local lifecycle event. These make a capture's own gaps visible; a replay
    that silently glossed over a two-second reconnect would look like a clean
    session and invalidate any staleness analysis run against it.

Encoding and decoding live together here so the two sides cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from l2tca.feed.messages import RawMessage
from l2tca.feed.source import ControlEvent

__all__ = [
    "RECORDING_FORMAT_VERSION",
    "RecordingFormatError",
    "RecordingHeader",
    "ReplayRecord",
    "decode",
    "encode_control",
    "encode_header",
    "encode_message",
]

#: Bumped whenever the layout above changes incompatibly.
RECORDING_FORMAT_VERSION = 1


class RecordingFormatError(ValueError):
    """The file is not a recording this build understands."""


@dataclass(frozen=True, slots=True)
class RecordingHeader:
    """Metadata captured once, at the top of every recording file."""

    v: int
    symbol: str
    depth: int
    url: str
    started_wall_ns: int
    #: ``perf_counter_ns()`` and ``time_ns()`` read back to back at open.
    perf_epoch_ns: int
    perf_epoch_wall_ns: int

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> RecordingHeader:
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


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """One decoded line: exactly one of the three fields is set."""

    header: RecordingHeader | None = None
    message: RawMessage | None = None
    control: ControlEvent | None = None


def encode_header(header: RecordingHeader) -> dict[str, Any]:
    return {
        "v": RECORDING_FORMAT_VERSION,
        "kind": "header",
        "symbol": header.symbol,
        "depth": header.depth,
        "url": header.url,
        "started_wall_ns": header.started_wall_ns,
        "perf_epoch_ns": header.perf_epoch_ns,
        "perf_epoch_wall_ns": header.perf_epoch_wall_ns,
    }


def encode_message(message: RawMessage) -> dict[str, Any]:
    return {
        "v": RECORDING_FORMAT_VERSION,
        "kind": "msg",
        "seq": message.seq,
        "recv_ns": message.recv_ns,
        "recv_wall_ns": message.recv_wall_ns,
        "payload": message.payload,
    }


def encode_control(event: ControlEvent) -> dict[str, Any]:
    return {
        "v": RECORDING_FORMAT_VERSION,
        "kind": "control",
        "event": event.event,
        "recv_ns": event.recv_ns,
        "recv_wall_ns": event.recv_wall_ns,
        "attempt": event.attempt,
        "detail": event.detail,
    }


def decode(obj: dict[str, Any]) -> ReplayRecord | None:
    """Turn one decoded line into a record, or ``None`` for a kind we skip."""
    kind = obj.get("kind")
    if kind == "header":
        header = RecordingHeader.from_dict(obj)
        if header.v > RECORDING_FORMAT_VERSION:
            raise RecordingFormatError(
                f"recording format v{header.v} is newer than this build "
                f"(v{RECORDING_FORMAT_VERSION})"
            )
        return ReplayRecord(header=header)
    if kind == "msg":
        return ReplayRecord(
            message=RawMessage(
                seq=int(obj["seq"]),
                recv_ns=int(obj["recv_ns"]),
                recv_wall_ns=int(obj["recv_wall_ns"]),
                payload=obj["payload"],
            )
        )
    if kind == "control":
        return ReplayRecord(
            control=ControlEvent(
                event=str(obj["event"]),
                recv_ns=int(obj["recv_ns"]),
                recv_wall_ns=int(obj["recv_wall_ns"]),
                attempt=int(obj.get("attempt", 0)),
                detail=str(obj.get("detail", "")),
            )
        )
    return None
