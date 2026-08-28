"""``l2tca synth`` -- generate a deterministic capture with no network."""

from __future__ import annotations

import argparse
import time

from l2tca.config import FeedConfig
from l2tca.feed.messages import RawMessage
from l2tca.feed.recorder import JsonlRecorder
from l2tca.feed.synthetic import synthetic_session

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
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

    print(f"wrote {recorder.records} records ({recorder.bytes_written / 1e6:.1f} MB) -> {args.out}")
    print("synthetic data: shape only, no market meaning. Do not draw conclusions.")
    return 0
