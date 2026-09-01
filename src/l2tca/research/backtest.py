"""Replay a TWAP execution across many windows of a capture, and price it.

An **execution** backtest, not a strategy backtest. It answers "if this order
had to be worked over this window, what would it have cost against the standard
benchmarks", and it makes no claim about whether trading was a good idea. That
distinction is why it is defensible on a few hours of data: there is no alpha
being estimated, so there is no out-of-sample question to fail.

What it deliberately does not do
--------------------------------
No profit and loss, no position, no signal. A strategy backtest on this data
would be indefensible on two counts, both structural rather than a matter of
effort: hours of one symbol cannot support a P&L estimate, and L2 data carries
no queue position, so a resting order's fill cannot be simulated at all. That
leaves aggressive execution, which pays the spread every time -- and the spread
measured on this venue is one tick at the median with a negative realized
component, which is a structural cost rather than a strategy.

One window is an anecdote. Running the same schedule across many windows spread
over the session gives a distribution, and the spread of that distribution is
the honest headline: an execution algorithm is judged on its tail, not its mean.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from l2tca.book.types import BookView, Side
from l2tca.feed.messages import BookSnapshot, BookUpdate, Trades
from l2tca.feed.parser import parse
from l2tca.feed.replay import iter_raw_messages
from l2tca.io.derive import iter_book_views
from l2tca.tca.analysis import (
    arrival_price,
    attribute_slippage,
    interval_vwap,
    simulate_child_orders,
)
from l2tca.tca.types import Order

__all__ = ["run_window", "run_windows"]


def run_window(
    order: Order,
    views: list[BookView],
    volumes: list[tuple[int, Decimal]],
    start_ns: int,
    end_ns: int,
    *,
    slices: int = 10,
) -> dict[str, Any]:
    """Work ``order`` across one window and price the result.

    Returns the filled quantity and average price, both benchmarks, the
    slippage against each in basis points, and the four attribution layers.
    Benchmarks that the window cannot support -- an interval VWAP with no trades
    in it, for instance -- come back as ``None`` rather than as a number derived
    from nothing.
    """
    fills = simulate_child_orders(order, views, start_ns, end_ns, slices=slices)
    filled = sum((f.qty for f in fills), Decimal(0))
    row: dict[str, Any] = {
        "start_ns": start_ns,
        "target_qty": float(order.target_qty),
        "filled_qty": float(filled),
        "fills": len(fills),
    }
    if not fills:
        return row

    notional = sum((f.price * f.qty for f in fills), Decimal(0))
    average = notional / filled
    direction = order.side.sign
    row["avg_price"] = float(average)

    arrival = arrival_price(order, views)
    row["arrival"] = float(arrival)
    row["vs_arrival_bps"] = float(direction * (average - arrival) / arrival * 10_000)

    in_window = [(ts, qty) for ts, qty in volumes if start_ns <= ts <= end_ns]
    try:
        vwap = interval_vwap(views, in_window, start_ns, end_ns)
    except ValueError:
        # No traded volume in this window, so there is no volume-weighted
        # benchmark. Reported as absent rather than filled in with the mid.
        row["interval_vwap"] = None
        row["vs_vwap_bps"] = None
    else:
        row["interval_vwap"] = float(vwap)
        row["vs_vwap_bps"] = float(direction * (average - vwap) / vwap * 10_000)

    row.update(attribute_slippage(order, fills, views))
    return row


def _span_and_volume(
    path: Path | str,
) -> tuple[int, int, list[tuple[int, Decimal]]]:
    """First and last book-frame stamp, and every live trade, in one cheap pass.

    Parsing only -- no book is rebuilt. Reconstruction is the expensive part of
    a replay, and neither the window layout nor the volume series needs it, so
    this pass costs a fraction of the one that follows.
    """
    first_ns: int | None = None
    last_ns = 0
    volumes: list[tuple[int, Decimal]] = []
    for message in iter_raw_messages(path):
        frame = parse(message.payload)
        if isinstance(frame, BookSnapshot | BookUpdate):
            if first_ns is None:
                first_ns = message.recv_ns
            last_ns = message.recv_ns
        elif isinstance(frame, Trades) and not frame.is_snapshot:
            volumes.extend((message.recv_ns, trade.qty) for trade in frame.trades)
    if first_ns is None:
        raise ValueError("capture contains no book frames")
    return first_ns, last_ns, volumes


def run_windows(
    path: Path | str,
    *,
    symbol: str = "BTC/USD",
    side: Side = Side.BID,
    qty: Decimal = Decimal("1"),
    windows: int = 20,
    duration_ns: int = 60_000_000_000,
    slices: int = 10,
    depth: int = 100,
) -> Iterator[dict[str, Any]]:
    """Run the same schedule across ``windows`` evenly spaced windows.

    Two passes over the capture: a cheap one that parses without rebuilding the
    book, to lay out the windows and collect traded volume, and one that
    reconstructs. Views are held only for the windows still open plus the last
    view before each -- a six-hour capture is two million views, and the arrival
    benchmark needs exactly one of them from before the window opens.
    """
    if windows < 1:
        raise ValueError(f"need at least one window, got {windows}")

    first_ns, last_ns, volumes = _span_and_volume(path)
    span_ns = last_ns - first_ns
    if span_ns <= duration_ns:
        raise ValueError(
            f"capture spans {span_ns / 1e9:.1f}s, shorter than one "
            f"{duration_ns / 1e9:.0f}s window"
        )

    step = (span_ns - duration_ns) // (windows - 1) if windows > 1 else 0
    starts = [first_ns + i * step for i in range(windows)]

    buffers: dict[int, list[BookView]] = {s: [] for s in starts}
    carried: dict[int, BookView | None] = dict.fromkeys(starts)
    last_view: BookView | None = None

    for _message, view in iter_book_views(path, symbol=symbol, depth=depth):
        t = view.recv_ns
        # Windows containing t are those whose start lies in [t - duration, t].
        # Found by bisecting the sorted starts rather than scanning all of them:
        # the scan is two million views by every window.
        lo = bisect.bisect_left(starts, t - duration_ns)
        hi = bisect.bisect_right(starts, t)
        for s in starts[lo:hi]:
            if not buffers[s]:
                carried[s] = last_view
            buffers[s].append(view)
        last_view = view

    for s in starts:
        views = buffers[s]
        if carried[s] is not None:
            views = [carried[s], *views]
        if not views:
            continue
        order = Order(symbol, side, qty, decision_ns=s)
        try:
            yield run_window(order, views, volumes, s, s + duration_ns, slices=slices)
        except ValueError:
            # A window with no usable book at its decision instant is skipped
            # rather than reported with a benchmark reached forward for.
            continue
