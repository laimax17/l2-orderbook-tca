"""``l2tca inspect`` -- summarise a capture."""

from __future__ import annotations

import argparse
import time
from collections import Counter

from l2tca.feed.messages import BookSnapshot, BookUpdate
from l2tca.feed.parser import parse
from l2tca.feed.replay import iter_records, read_header

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
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
    rate = f"  ({total / span_s:.1f} frames/s)" if span_s > 0 else ""
    print(f"span          : {span_s:.1f}s{rate}")
    print(f"largest gap   : {max_gap_ns / 1e6:.1f} ms")
    print(f"seq gaps      : {seq_gaps}")
    print(f"price levels  : {levels}")
    for name, count in kinds.most_common():
        print(f"  {name:<20}{count:>10}")
    for name, count in controls.most_common():
        print(f"  control:{name:<12}{count:>10}")
    return 0
