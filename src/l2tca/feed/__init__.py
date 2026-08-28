"""WebSocket ingest: connection management, message parsing, recording, replay."""

from l2tca.feed.client import KrakenFeedClient
from l2tca.feed.messages import (
    BookLevel,
    BookSnapshot,
    BookUpdate,
    ErrorMessage,
    Heartbeat,
    ParsedMessage,
    Pong,
    RawMessage,
    Status,
    SubscriptionAck,
    Unknown,
)
from l2tca.feed.parser import parse
from l2tca.feed.recorder import JsonlRecorder, default_capture_path
from l2tca.feed.records import RECORDING_FORMAT_VERSION, RecordingHeader
from l2tca.feed.replay import ReplaySource, iter_raw_messages, iter_records, read_header
from l2tca.feed.source import ControlEvent, FeedStats, MessageSource
from l2tca.feed.subscription import StaleConnectionError, SubscriptionError

__all__ = [
    "RECORDING_FORMAT_VERSION",
    "BookLevel",
    "BookSnapshot",
    "BookUpdate",
    "ControlEvent",
    "ErrorMessage",
    "FeedStats",
    "Heartbeat",
    "JsonlRecorder",
    "KrakenFeedClient",
    "MessageSource",
    "ParsedMessage",
    "Pong",
    "RawMessage",
    "RecordingHeader",
    "ReplaySource",
    "StaleConnectionError",
    "Status",
    "SubscriptionAck",
    "SubscriptionError",
    "Unknown",
    "default_capture_path",
    "iter_raw_messages",
    "iter_records",
    "parse",
    "read_header",
]
