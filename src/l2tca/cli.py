"""Command line entry point.

    l2tca record   --duration 600            capture a live session to JSONL
    l2tca synth    --updates 5000            generate a capture without a network
    l2tca inspect  <file>                    summarise a capture
    l2tca replay   <file> --speed 10         replay it, paced or as fast as possible
    l2tca convert  <file> --out data/parquet flatten it into the tick table
    l2tca bench    <file>                    time parse/apply/view per call

Argparse rather than a CLI framework: it is in the standard library, and the
dependency list of a data-capture tool is a thing worth keeping short.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time
from collections import Counter
from pathlib import Path

from l2tca import __version__
from l2tca.config import VALID_DEPTHS, FeedConfig, Paths, symbol_to_path_token

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="l2tca",
        description="L2 order book reconstruction and execution cost analysis.",
    )
    parser.add_argument("--version", action="version", version=f"l2tca {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_feed_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--symbol", default="BTC/USD", help="pair, e.g. BTC/USD (XBT/USD accepted)")
        p.add_argument("--depth", type=int, default=100, choices=VALID_DEPTHS)

    rec = sub.add_parser("record", help="capture a live session to data/raw")
    add_feed_args(rec)
    rec.add_argument("--duration", type=float, default=600.0, help="seconds; 0 means until Ctrl-C")
    rec.add_argument("--out", type=Path, default=None, help="output file (default: auto-named)")
    rec.add_argument("--dir", type=Path, default=None, help="output directory for auto-naming")
    rec.add_argument("--compress", action="store_true", help="write .jsonl.gz")
    rec.add_argument("--url", default=None, help="override the WebSocket endpoint")

    syn = sub.add_parser("synth", help="generate a deterministic synthetic capture")
    add_feed_args(syn)
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

    ben = sub.add_parser("bench", help="time parse/apply/view against a capture")
    ben.add_argument("file", type=Path)
    add_feed_args(ben)
    ben.add_argument("--limit", type=int, default=None)
    ben.add_argument("--warmup", type=int, default=500)
    ben.add_argument("--view-levels", type=int, default=10)
    ben.add_argument("--json", dest="as_json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "record": _cmd_record,
        "synth": _cmd_synth,
        "inspect": _cmd_inspect,
        "replay": _cmd_replay,
        "convert": _cmd_convert,
        "bench": _cmd_bench,
    }
    return handlers[args.command](args)


# -- commands --------------------------------------------------------------


def _cmd_record(args: argparse.Namespace) -> int:
    from l2tca.feed.recorder import JsonlRecorder, default_capture_path

    config = FeedConfig(
        symbol=args.symbol,
        depth=args.depth,
        **({"url": args.url} if args.url else {}),
    )
    directory = args.dir or Paths().raw
    path = args.out or default_capture_path(directory, config, compress=args.compress)
    recorder = JsonlRecorder(path, config)

    print(f"recording {config.wire_symbol} depth={config.depth} -> {path}", file=sys.stderr)
    if args.duration:
        print(f"stopping after {args.duration:.0f}s (Ctrl-C to stop early)", file=sys.stderr)
    else:
        print("stopping on Ctrl-C", file=sys.stderr)

    try:
        stats = asyncio.run(_record(config, recorder, args.duration))
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        print("\ninterrupted", file=sys.stderr)
        return 130

    print(
        f"wrote {recorder.records} records, {recorder.bytes_written / 1e6:.1f} MB "
        f"({stats.messages} frames, {stats.reconnects} reconnects) -> {path}",
        file=sys.stderr,
    )
    return 0


async def _record(config: FeedConfig, recorder, duration: float):
    from l2tca.feed.kraken import KrakenFeedClient

    client = KrakenFeedClient(config, on_control=recorder.write_control)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, stop.set)

    async def pump() -> None:
        # aclosing() rather than a bare async-for: the generator owns a socket,
        # and letting the garbage collector close it means the close frame is
        # never sent and the recording ends without its final flush.
        async with contextlib.aclosing(client.stream()) as stream:
            async for message in stream:
                recorder.write_message(message)
                if stop.is_set():
                    break

    with recorder:
        task = asyncio.create_task(pump())
        waiters: list[asyncio.Future] = [task, asyncio.ensure_future(stop.wait())]
        timeout = duration if duration and duration > 0 else None
        done, pending = await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        stop.set()
        await client.aclose()
        for future in pending:
            future.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for future in done:
            if future is task and future.exception() is not None:
                raise future.exception()  # type: ignore[misc]

    return client.stats


def _cmd_synth(args: argparse.Namespace) -> int:
    from l2tca.feed.messages import RawMessage
    from l2tca.feed.recorder import JsonlRecorder
    from l2tca.feed.synthetic import synthetic_session

    config = FeedConfig(symbol=args.symbol, depth=args.depth)
    recorder = JsonlRecorder(args.out, config)
    origin_perf = time.perf_counter_ns()
    origin_wall = time.time_ns()

    with recorder:
        for seq, (offset, payload) in enumerate(
            synthetic_session(
                symbol=config.wire_symbol,
                depth=config.depth,
                updates=args.updates,
                seed=args.seed,
            )
        ):
            delta = int(offset * 1e9)
            recorder.write_message(
                RawMessage(
                    seq=seq,
                    recv_ns=origin_perf + delta,
                    recv_wall_ns=origin_wall + delta,
                    payload=payload,
                )
            )

    print(
        f"wrote {recorder.records} records ({recorder.bytes_written / 1e6:.1f} MB) -> {args.out}",
        file=sys.stderr,
    )
    print(
        "synthetic data: shape only, no market meaning. Do not draw conclusions.",
        file=sys.stderr,
    )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    from l2tca.feed.messages import BookSnapshot, BookUpdate, parse
    from l2tca.feed.replay import iter_records, read_header

    header = read_header(args.file)
    kinds: Counter[str] = Counter()
    controls: Counter[str] = Counter()
    first_ns = last_ns = None
    max_gap_ns = 0
    seq_gaps = 0
    expected_seq = None
    levels = 0

    for record in iter_records(args.file):
        if record.control is not None:
            controls[record.control.event] += 1
            continue
        message = record.message
        if message is None:
            continue
        if first_ns is None:
            first_ns = message.recv_ns
        elif last_ns is not None:
            max_gap_ns = max(max_gap_ns, message.recv_ns - last_ns)
        last_ns = message.recv_ns

        if expected_seq is not None and message.seq != expected_seq:
            seq_gaps += 1
        expected_seq = message.seq + 1

        parsed = parse(message.payload)
        kinds[type(parsed).__name__] += 1
        if isinstance(parsed, BookSnapshot | BookUpdate):
            levels += len(parsed.bids) + len(parsed.asks)

    span_s = (last_ns - first_ns) / 1e9 if first_ns is not None and last_ns is not None else 0.0
    total = sum(kinds.values())

    print(f"file          : {args.file}")
    if header is not None:
        started = header.started_wall_ns / 1e9
        print(f"header        : v{header.v} {header.symbol} depth={header.depth} {header.url}")
        print(f"started       : {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(started))}")
    else:
        print("header        : (none -- not written by this tool, or truncated)")
    print(f"frames        : {total}")
    print(f"span          : {span_s:.1f}s"
          f"{f'  ({total / span_s:.1f} frames/s)' if span_s > 0 else ''}")
    print(f"largest gap   : {max_gap_ns / 1e6:.1f} ms")
    print(f"seq gaps      : {seq_gaps}")
    print(f"price levels  : {levels}")
    for name, count in kinds.most_common():
        print(f"  {name:<20}{count:>10}")
    for name, count in controls.most_common():
        print(f"  control:{name:<12}{count:>10}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    from l2tca.feed.replay import ReplaySource

    source = ReplaySource(args.file, speed=args.speed, limit=args.limit)

    async def run() -> int:
        count = 0
        start = time.perf_counter_ns()
        async with contextlib.aclosing(source.stream()) as stream:
            async for message in stream:
                count += 1
                if args.do_print:
                    print(message.payload)
        elapsed = (time.perf_counter_ns() - start) / 1e9
        summary = (
            f"replayed {count} frames in {elapsed:.3f}s ({count / elapsed:,.0f} frames/s)"
            if elapsed > 0
            else f"replayed {count} frames"
        )
        print(summary, file=sys.stderr)
        return 0

    return asyncio.run(run())


def _cmd_convert(args: argparse.Namespace) -> int:
    from l2tca.io.convert import iter_tick_rows
    from l2tca.io.writer import PartitionedParquetWriter

    root = args.out or Paths().parquet
    token = symbol_to_path_token("BTC/USD")

    rows = iter_tick_rows(args.file, limit=args.limit)
    first = next(rows, None)
    if first is None:
        print("no book frames in this capture; nothing written", file=sys.stderr)
        return 1
    token = first["symbol"]

    with PartitionedParquetWriter(
        root, "tick", token, max_rows_per_flush=args.rows_per_file
    ) as writer:
        writer.write_row(first)
        writer.write_rows(rows)

    print(
        f"wrote {writer.rows_written} tick rows across {writer.files_written} files "
        f"-> {Path(root) / 'tick'}",
        file=sys.stderr,
    )
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    from l2tca.bench.harness import format_report, run_book_benchmark

    report = run_book_benchmark(
        args.file,
        symbol=args.symbol,
        depth=args.depth,
        limit=args.limit,
        warmup=args.warmup,
        view_levels=args.view_levels,
    )
    print(report.to_json() if args.as_json else format_report(report))
    if any(stage.note == "not implemented" for stage in report.stages):
        print(
            "\nbook stages are unimplemented -- see docs/SPEC.md and src/l2tca/book/l2_book.py",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
