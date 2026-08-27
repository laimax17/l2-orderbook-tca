"""Execution cost analysis.

The cost maths is intentionally unimplemented -- see :mod:`l2tca.tca.execution`.
The order/fill value types here are concrete.

This package analyses execution *after the fact*, from recorded book data and a
supplied fill list. It contains no order entry, no venue connectivity and no
broker credentials, and it never will: the repository is read-only against the
exchange's public feed.
"""

from l2tca.tca.base import Fill, Order, Side, TcaResult
from l2tca.tca.execution import (
    arrival_slippage_bps,
    effective_spread_bps,
    implementation_shortfall_bps,
    participation_weighted_price,
    realized_spread_bps,
    twap_benchmark,
    walk_the_book_cost,
)

__all__ = [
    "Fill",
    "Order",
    "Side",
    "TcaResult",
    "arrival_slippage_bps",
    "effective_spread_bps",
    "implementation_shortfall_bps",
    "participation_weighted_price",
    "realized_spread_bps",
    "twap_benchmark",
    "walk_the_book_cost",
]
