"""``l2tca replay`` -- replay a capture, paced or as fast as possible."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

from l2tca.feed.replay import ReplaySource

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    source = ReplaySource(args.file, speed=args.speed, limit=args.limit)

    async def drive() -> int:
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
        print(summary)
        return 0

    return asyncio.run(drive())
