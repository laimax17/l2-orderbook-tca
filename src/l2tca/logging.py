"""Structured logging.

One line of JSON per event, on stderr, so a capture run can be piped into any
log tool without a parser. Built on the standard library -- a data-capture
process should not pull in a logging framework.

Human-facing command output (tables, summaries) goes to stdout via ``print``.
Logging is for *events*: connects, disconnects, reconnects, flushes, errors.
Keeping the two apart means ``l2tca inspect ... > report.txt`` stays clean and
``2> events.jsonl`` stays machine-readable.

Context is attached with the standard ``extra=`` argument::

    log.info("reconnect", extra={"attempt": 3, "delay_s": 1.7})

which lands as top-level keys in the emitted object.
"""

from __future__ import annotations

import json
import logging
import sys
import time

__all__ = ["JsonFormatter", "configure_logging", "get_logger"]

#: Attributes LogRecord always carries. Anything else came from ``extra=`` and
#: is treated as structured context.
_STANDARD_FIELDS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    | {"asctime", "message", "taskName"}
)


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS:
                payload[key] = value
        if record.exc_info:
            # The traceback goes in a field rather than trailing the line, so one
            # event stays one line.
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str = "info", *, json_output: bool = True) -> None:
    """Install a single stderr handler. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger("l2tca")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Logger under the ``l2tca`` root, so one call configures the whole package."""
    return logging.getLogger(name if name.startswith("l2tca") else f"l2tca.{name}")
