"""``l2tca record`` -- capture a live session."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys

from l2tca.config import FeedConfig, Paths
from l2tca.feed.client import KrakenFeedClient
from l2tca.feed.recorder import JsonlRecorder, default_capture_path

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    config = FeedConfig(
        symbol=args.symbol,
        depth=args.depth,
        trades=getattr(args, "trades", False),
        **({"url": args.url} if args.url else {}),
    )
    directory = args.dir or Paths().raw
    path = args.out or default_capture_path(directory, config, compress=args.compress)
    recorder = JsonlRecorder(path, config)

    print(f"recording {config.wire_symbol} depth={config.depth} -> {path}")
    print(
        f"stopping after {args.duration:.0f}s (Ctrl-C to stop early)"
        if args.duration
        else "stopping on Ctrl-C"
    )

    try:
        stats = asyncio.run(_record(config, recorder, args.duration))
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        print("\ninterrupted", file=sys.stderr)
        return 130

    print(
        f"wrote {recorder.records} records, {recorder.bytes_written / 1e6:.1f} MB "
        f"({stats.messages} frames, {stats.reconnects} reconnects) -> {path}"
    )
    return 0


async def _record(config: FeedConfig, recorder: JsonlRecorder, duration: float):
    client = KrakenFeedClient(config, on_control=recorder.write_control)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, stop.set)

    async def pump() -> None:
        # aclosing() rather than a bare async-for: the generator owns a socket,
        # and letting the garbage collector close it means the close frame is
        # never sent and the capture ends without its final flush.
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
