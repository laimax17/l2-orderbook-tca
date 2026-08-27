"""Microstructure signals computed from a reconstructed book.

The factor maths is intentionally unimplemented -- see
:mod:`l2tca.signals.microstructure`. The container types here are concrete so
the Parquet ``signal`` table and the CLI are complete already.
"""

from l2tca.signals.base import SignalRow, SignalSet
from l2tca.signals.microstructure import (
    book_pressure,
    depth_slope,
    log_depth_ratio,
    micro_price,
    order_book_imbalance,
    relative_spread_bps,
    weighted_mid,
)

__all__ = [
    "SignalRow",
    "SignalSet",
    "book_pressure",
    "depth_slope",
    "log_depth_ratio",
    "micro_price",
    "order_book_imbalance",
    "relative_spread_bps",
    "weighted_mid",
]
