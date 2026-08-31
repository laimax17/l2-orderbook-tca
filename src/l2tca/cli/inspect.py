"""``l2tca inspect`` -- summarise a capture."""

from __future__ import annotations

import argparse
import time
from collections import Counter

from l2tca.book.order_book import OrderBook
from l2tca.book.sequence import verify_checksum
from l2tca.feed.messages import BookSnapshot, BookUpdate
from l2tca.feed.parser import parse
from l2tca.feed.replay import iter_records, read_header

__all__ = ["run"]


class _Verifier:
    """Replays the book alongside the summary and checks each frame's CRC32.

    Kraken sends no per-frame sequence number, so the checksum is the only
    evidence that a reconstruction has not silently drifted. Counting how many
    frames agree is therefore a statement about the order book, not about the
    capture -- which is why it is opt-in rather than part of every summary.
    """

    def __init__(self, symbol: str, depth: int, price_precision: int, qty_precision: int) -> None:
        self.book = OrderBook(symbol, depth=depth)
        self.precisions = (price_precision, qty_precision)
        self.checked = 0
        self.agreed = 0
        self.uncheckable = 0
        self.ready = False

    def feed(self, frame: BookSnapshot | BookUpdate) -> None:
        if isinstance(frame, BookSnapshot):
            self.book.apply_snapshot(frame)
            self.ready = True
            return
        if not self.ready:
            self.uncheckable += 1  # updates before the opening snapshot
            return
        self.book.apply_update(frame)
        if frame.checksum is None:
            self.uncheckable += 1
            return
        bids, asks = self.book.depth_levels(10)
        self.checked += 1
        self.agreed += verify_checksum(bids, asks, frame.checksum, *self.precisions)

    def report(self) -> str:
        if not self.checked:
            return "checksums     : none to check (no snapshot, or no checksum field)"
        rate = 100.0 * self.agreed / self.checked
        skipped = f", {self.uncheckable} uncheckable" if self.uncheckable else ""
        return f"checksums     : {self.agreed}/{self.checked} verified ({rate:.2f}%){skipped}"


def run(args: argparse.Namespace) -> int:
    header = read_header(args.file)
    kinds: Counter[str] = Counter()
    controls: Counter[str] = Counter()
    first_ns = last_ns = None
    max_gap_ns = 0
    seq_gaps = 0
    expected_seq = None
    levels = 0
    verifier = (
        _Verifier(args.symbol, args.depth, args.price_precision, args.qty_precision)
        if getattr(args, "verify", False)
        else None
    )

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
            if verifier is not None:
                verifier.feed(parsed)

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
    if verifier is not None:
        print(verifier.report())
    for name, count in kinds.most_common():
        print(f"  {name:<20}{count:>10}")
    for name, count in controls.most_common():
        print(f"  control:{name:<12}{count:>10}")
    return 0
