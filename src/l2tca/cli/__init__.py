"""Command line entry point.

    l2tca record   --duration 600            capture a live session to JSONL
    l2tca synth    --updates 5000            generate a capture without a network
    l2tca inspect  <file>                    summarise a capture
    l2tca replay   <file> --speed 10         replay it, paced or as fast as possible
    l2tca convert  <file> --out data/parquet flatten it into the tick table
    l2tca bench    <file>                    time recv -> book-updated, per stage
    l2tca plot     depth|spread|latency      render a figure to a PNG

argparse rather than a CLI framework: it is in the standard library, and the
dependency list of a data-capture tool is worth keeping short.

Human-facing output goes to stdout; lifecycle events go to the structured
logger on stderr (see :mod:`l2tca.logging`), so ``> report.txt`` stays clean and
``2> events.jsonl`` stays machine-readable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from l2tca import __version__
from l2tca.cli import bench, convert, inspect, plot, record, replay, synth
from l2tca.config import VALID_DEPTHS
from l2tca.logging import configure_logging

__all__ = ["build_parser", "main"]

_HANDLERS = {
    "record": record.run,
    "synth": synth.run,
    "inspect": inspect.run,
    "replay": replay.run,
    "convert": convert.run,
    "bench": bench.run,
    "plot": plot.run,
}


def _add_feed_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbol", default="BTC/USD", help="pair, e.g. BTC/USD (XBT/USD accepted)")
    p.add_argument("--depth", type=int, default=100, choices=VALID_DEPTHS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="l2tca",
        description="L2 order book reconstruction and execution cost analysis.",
    )
    parser.add_argument("--version", action="version", version=f"l2tca {__version__}")
    parser.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    parser.add_argument(
        "--log-text", action="store_true", help="human-readable logs instead of JSON"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="capture a live session to data/raw")
    _add_feed_args(rec)
    rec.add_argument("--duration", type=float, default=600.0, help="seconds; 0 means until Ctrl-C")
    rec.add_argument("--out", type=Path, default=None, help="output file (default: auto-named)")
    rec.add_argument("--dir", type=Path, default=None, help="output directory for auto-naming")
    rec.add_argument("--compress", action="store_true", help="write .jsonl.gz")
    rec.add_argument("--url", default=None, help="override the WebSocket endpoint")

    syn = sub.add_parser("synth", help="generate a deterministic synthetic capture")
    _add_feed_args(syn)
    syn.add_argument("--updates", type=int, default=2000)
    syn.add_argument("--seed", type=int, default=7)
    syn.add_argument("--out", type=Path, required=True)

    ins = sub.add_parser("inspect", help="summarise a capture")
    ins.add_argument("file", type=Path)

    rep = sub.add_parser("replay", help="replay a capture")
    rep.add_argument("file", type=Path)
    rep.add_argument("--speed", type=float, default=0.0, help="0 = as fast as possible")
    rep.add_argument("--limit", type=int, default=None)
    rep.add_argument("--print", dest="do_print", action="store_true", help="echo each payload")

    con = sub.add_parser("convert", help="flatten a capture into the tick Parquet table")
    con.add_argument("file", type=Path)
    con.add_argument("--out", type=Path, default=None, help="parquet root (default: data/parquet)")
    con.add_argument("--limit", type=int, default=None, help="stop after N book frames")
    con.add_argument("--rows-per-file", type=int, default=250_000)

    ben = sub.add_parser("bench", help="time recv -> book-updated against a capture")
    ben.add_argument("file", type=Path)
    _add_feed_args(ben)
    ben.add_argument("--limit", type=int, default=None)
    ben.add_argument("--warmup", type=int, default=500)
    ben.add_argument("--view-levels", type=int, default=10)
    ben.add_argument("--histogram", action="store_true", help="print per-stage histograms")
    ben.add_argument("--json", dest="as_json", action="store_true")

    plo = sub.add_parser("plot", help="render a figure to a PNG")
    plo.add_argument("kind", choices=["depth", "spread", "latency"])
    plo.add_argument("--root", type=Path, default=None, help="parquet root, for depth/spread")
    plo.add_argument("--report", type=Path, default=None, help="bench JSON, for latency")
    plo.add_argument("--symbol-token", default=None, help="e.g. BTC-USD")
    plo.add_argument("--stage", default=None, help="stage name, for latency")
    plo.add_argument("--out", type=Path, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, json_output=not args.log_text)
    return _HANDLERS[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
