"""Value types for execution cost analysis.

Concrete and complete: the cost functions are stubs, but the data they consume
and produce is settled, so the CLI and the Parquet layer already work end to
end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from l2tca.book.base import Side

__all__ = ["Fill", "Order", "Side", "TcaResult"]


@dataclass(frozen=True, slots=True)
class Order:
    """The parent order whose execution is being measured.

    Attributes:
        arrival_ns: Monotonic stamp of the decision instant. Every
            shortfall number is measured against the book as it stood *here*,
            so this is the single most consequential field: move it and the
            attribution between "delay cost" and "impact cost" moves with it.
        target_qty: Total intended quantity, positive regardless of side.
    """

    symbol: str
    side: Side
    target_qty: Decimal
    arrival_ns: int
    arrival_wall_ns: int = 0
    order_id: str = ""


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution against the parent order."""

    ts_ns: int
    price: Decimal
    qty: Decimal
    #: Exchange fee in quote currency, positive for a cost, negative for a rebate.
    fee: Decimal = Decimal(0)
    #: ``True`` when this fill removed liquidity. Drives the maker/taker split.
    is_taker: bool = True
    fill_id: str = ""


@dataclass(slots=True)
class TcaResult:
    """The cost decomposition for one parent order.

    All ``*_bps`` figures are signed so that **positive means cost** and
    negative means price improvement, on both sides of the market. Getting that
    sign convention consistent across buys and sells is the detail that most
    often makes a TCA report quietly meaningless.
    """

    order_id: str
    symbol: str
    side: Side
    filled_qty: Decimal
    average_price: Decimal
    arrival_mid: Decimal | None = None
    implementation_shortfall_bps: float = float("nan")
    arrival_slippage_bps: float = float("nan")
    effective_spread_bps: float = float("nan")
    realized_spread_bps: float = float("nan")
    price_impact_bps: float = float("nan")
    fee_bps: float = float("nan")
    components: dict[str, float] = field(default_factory=dict)
