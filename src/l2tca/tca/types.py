"""Value types for execution cost analysis.

Deliberately thin. There is no result type here with named attribution fields,
because how slippage decomposes is one of the open design questions in
:mod:`l2tca.tca.analysis` -- shipping a container with the layers already named
would answer it.

This package analyses execution *after the fact*, from recorded book data and a
supplied fill list. It contains no order entry, no venue connectivity and no
broker credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from l2tca.book.types import Side

__all__ = ["Fill", "Order", "Side"]


@dataclass(frozen=True, slots=True)
class Order:
    """The parent order whose execution is being measured.

    Attributes:
        target_qty: Total intended quantity, positive regardless of side.
        decision_ns: Monotonic stamp of the moment the order was decided on.
            Named for the event, not for a benchmark: which price at which
            instant becomes the arrival benchmark is an open question in
            :mod:`l2tca.tca.analysis`, and naming this field ``arrival_*``
            would prejudge it.
    """

    symbol: str
    side: Side
    target_qty: Decimal
    decision_ns: int
    decision_wall_ns: int = 0
    order_id: str = ""


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution against the parent order."""

    ts_ns: int
    price: Decimal
    qty: Decimal
    #: Exchange fee in quote currency, positive for a cost, negative for a rebate.
    fee: Decimal = Decimal(0)
    #: ``True`` when this fill removed liquidity.
    is_taker: bool = True
    fill_id: str = ""
