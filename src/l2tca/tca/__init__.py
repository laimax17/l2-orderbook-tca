"""Execution cost analysis.

The cost maths is core logic and is not implemented; see
:mod:`l2tca.tca.analysis`.
"""

from l2tca.tca.analysis import (
    arrival_price,
    attribute_slippage,
    interval_vwap,
    simulate_child_orders,
)
from l2tca.tca.types import Fill, Order, Side

__all__ = [
    "Fill",
    "Order",
    "Side",
    "arrival_price",
    "attribute_slippage",
    "interval_vwap",
    "simulate_child_orders",
]
