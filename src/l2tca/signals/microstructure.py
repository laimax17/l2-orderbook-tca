"""Microstructure signals. CORE LOGIC -- NOT IMPLEMENTED.

Each function raises :class:`NotImplementedError`. The docstrings give the
mathematical definition and what the quantity means economically;
``tests/test_microstructure.py`` pins the expected values and is currently red.

All four take a :class:`~l2tca.book.types.BookView`, which is an immutable copy,
so they are pure functions of their input: same view in, same number out, every
replay. Keep them that way -- a factor that reads a clock or carries state
cannot be validated against a recording.

Inputs are :class:`decimal.Decimal` (exact prices); outputs are ``float``
(research values).

Every one of these has inputs on which it is not defined -- an empty side, a
zero total quantity, a zero mid. Decide what a caller gets in those cases, and
be consistent across all four. Whatever you choose, note that ``0.0`` is also a
legitimate result for several of these.
"""

from __future__ import annotations

from decimal import Decimal

from l2tca.book.types import BookView, Side

__all__ = [
    "effective_spread",
    "micro_price",
    "order_book_imbalance",
    "quoted_spread",
]


def order_book_imbalance(view: BookView, levels: int = 1) -> float:
    r"""Queue imbalance over the top ``levels`` of each side.

    .. math::

        I = \frac{Q_b - Q_a}{Q_b + Q_a}

    where :math:`Q_b` and :math:`Q_a` are the summed resting quantities on the
    bid and ask sides over those levels. Bounded to :math:`[-1, +1]`.

    Economic meaning: the relative pressure of resting buy interest against
    resting sell interest. Because a marketable order consumes the opposite
    side, a book that is heavy on the bid is one where the ask is easier to
    exhaust, and the mid tends to move up. It is the standard short-horizon
    predictor of the next mid change, and its predictive power falls off as
    ``levels`` grows -- deeper quantity is less likely to trade and cheaper to
    post, so it is both less informative and easier to fake.
    """
    bids = view.bids
    asks = view.asks

    qty_asks = 0
    qty_bids = 0

    for i in range(levels):
        if i < len(bids):
            qty_bids += float(bids[i].qty)
        if i < len(asks):
            qty_asks += float(asks[i].qty)
    if qty_bids == 0 and qty_asks == 0:
        return float('nan')
    return (qty_bids - qty_asks) / (qty_asks + qty_bids)


def micro_price(view: BookView) -> float:
    r"""Size-weighted best price.

    .. math::

        P_{micro} = \frac{P_b Q_a + P_a Q_b}{Q_a + Q_b}

    Note the weighting: the bid *price* is weighted by the ask *quantity*, and
    vice versa.

    Economic meaning: an estimate of fair value that accounts for how the queue
    is distributed, rather than splitting the spread blindly. The mid assumes
    the next trade is equally likely on either side; the micro-price says the
    thinner side is the one that gets consumed first, and leans the estimate
    toward the price that side will reach. It reduces to the arithmetic mid
    when the two sizes are equal.
    """
    bids = view.bids
    asks = view.asks
    if not bids or not asks:
        return float('nan')
    bid_price = float(bids[0].price)
    ask_price = float(asks[0].price)
    bid_qty = float(bids[0].qty)
    ask_qty = float(asks[0].qty)

    return ((bid_price * ask_qty) + (ask_price * bid_qty)) / (bid_qty + ask_qty)



def quoted_spread(view: BookView, *, in_bps: bool = True) -> float:
    r"""The advertised cost of an immediate round trip at the touch.

    .. math::

        S_{quoted} = P_a - P_b
        \qquad\text{or, in basis points,}\qquad
        10^4 \cdot \frac{P_a - P_b}{P_{mid}}

    Economic meaning: what a liquidity taker pays to buy and immediately sell
    one unit at the touch, and equivalently what a two-sided market maker earns
    per round trip if the price does not move. It is a *quoted* cost -- the
    price advertised for a trade of the size resting at the touch, and no
    larger. Expressing it in basis points makes it comparable across price
    regimes; the absolute number is not.
    """
    bids = view.bids
    asks = view.asks
    if not bids or not asks:
        return float('nan')
    bid_price = float(bids[0].price)
    ask_price = float(asks[0].price)

    if in_bps:
        mid_price = (bid_price + ask_price) / 2
        return 10000 * (ask_price - bid_price) / mid_price
    else:
        return ask_price - bid_price


def effective_spread(
    view: BookView,
    fill_price: Decimal,
    side: Side,
    *,
    in_bps: bool = True,
) -> float:
    r"""The spread a trade actually paid, measured against the contemporaneous mid.

    .. math::

        S_{eff} = 2 \cdot d \cdot (P_{fill} - P_{mid})

    where :math:`d` is :math:`+1` for a buy and :math:`-1` for a sell, so the
    result is positive when the trade paid away from the mid. The factor of two
    makes it comparable to the quoted spread rather than to a half-spread.

    Economic meaning: the realised cost of one execution, as opposed to the
    advertised one. It differs from the quoted spread in both directions and
    each direction is informative -- narrower means the trade was filled inside
    the touch, wider means it consumed more than the touch could supply.
    Comparing effective against quoted across many fills is the standard
    measure of whether an execution strategy is paying the advertised price.

    ``view`` must be the book state contemporaneous with the fill. Which
    instant that is -- and what "contemporaneous" means when book updates and
    fills arrive on different paths -- is part of the TCA design work in
    :mod:`l2tca.tca.analysis`.
    """
    if not view.bids or not view.asks:
        return float('nan')
    p_b = float(view.bids[0].price)
    p_a = float(view.asks[0].price)
    p_mid = (p_a + p_b) / 2.0

    p_fill = float(fill_price)

    d = 1.0 if side == Side('bid') else -1.0

    eff_spread = 2.0 * d * (p_fill - p_mid)

    if not in_bps:
        return eff_spread

    if p_mid == 0:
        return 0.0

    return 10000.0 * (eff_spread / p_mid)
