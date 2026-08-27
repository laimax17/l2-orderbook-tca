"""Microstructure factors -- CORE LOGIC, INTENTIONALLY UNIMPLEMENTED.

Each function below raises :class:`NotImplementedError`; the docstring is its
specification and ``tests/spec/test_signals_spec.py`` is the executable version.

All of them take a :class:`~l2tca.book.base.BookView`, which is an immutable
copy, so they are pure functions of their input: same view in, same number out,
every replay. Keep them that way -- a factor that reads a clock or mutates state
cannot be tested against a recording.

Numeric convention: inputs are ``Decimal`` (exact prices), outputs are ``float``
(research values). Convert once, at the end, so intermediate cancellation
happens in exact arithmetic.

Edge cases every one of these must handle rather than raise on: an empty side, a
side thinner than the requested level count, and zero total quantity. Returning
``float('nan')`` for "undefined here" is the convention used throughout, because
it propagates through downstream arithmetic instead of silently reading as zero.
"""

from __future__ import annotations

from l2tca.book.base import BookView

__all__ = [
    "book_pressure",
    "depth_slope",
    "log_depth_ratio",
    "micro_price",
    "order_book_imbalance",
    "relative_spread_bps",
    "weighted_mid",
]


def order_book_imbalance(view: BookView, levels: int = 1) -> float:
    """Queue imbalance over the top ``levels``: ``(B - A) / (B + A)``.

    ``B`` and ``A`` are the summed resting quantities on the bid and ask sides.
    Ranges over ``[-1, +1]``: ``+1`` is all bid, ``-1`` is all ask.

    The workhorse short-horizon predictor of mid-price movement. At
    ``levels=1`` it is the classic top-of-book imbalance; deeper sums are
    smoother but respond later, and the level count is the parameter worth
    sweeping against a recording.

    Returns ``nan`` when both sides are empty or the total quantity is zero.
    """
    raise NotImplementedError("core logic: implement by hand")


def micro_price(view: BookView) -> float:
    """Size-weighted best price: ``(P_b * Q_a + P_a * Q_b) / (Q_a + Q_b)``.

    Note the crossed weighting -- the *bid* price is weighted by the *ask*
    quantity. The intuition: a large resting ask means the book is heavy on the
    offer, so the next trade is likelier to be a sale into the bid, pulling the
    fair price toward the bid. Weighting each price by its own quantity is the
    common and wrong version, and it moves the estimate the wrong way.

    Reduces to the arithmetic mid when the two sizes are equal. Returns ``nan``
    if either side is empty or both sizes are zero.
    """
    raise NotImplementedError("core logic: implement by hand")


def weighted_mid(view: BookView, levels: int = 5) -> float:
    """Mid computed from quantity-weighted average prices over ``levels``.

    Take the quantity-weighted average price of the top ``levels`` bids and of
    the top ``levels`` asks, then average the two. Less jumpy than the top-of-
    book mid when the touch is thin, at the cost of reacting to depth that may
    never trade.

    Returns ``nan`` if either side is empty.
    """
    raise NotImplementedError("core logic: implement by hand")


def relative_spread_bps(view: BookView) -> float:
    """Quoted spread in basis points of the mid: ``1e4 * (P_a - P_b) / mid``.

    Basis points rather than currency so the number is comparable across price
    regimes -- a $10 spread on BTC at $20k and at $80k are different costs.

    Returns ``nan`` if either side is empty or the mid is zero.
    """
    raise NotImplementedError("core logic: implement by hand")


def book_pressure(view: BookView, levels: int = 10) -> float:
    """Distance-discounted imbalance over the top ``levels``.

    Weight each level's quantity by ``1 / (1 + |price - mid| / mid)`` before
    forming the same ``(B - A) / (B + A)`` ratio as
    :func:`order_book_imbalance`. Quantity resting far from the touch is
    unlikely to trade soon and is cheap to post, so counting it at face value
    makes the raw imbalance easy to spoof; discounting by distance blunts that.

    Returns ``nan`` when the mid is undefined or total weighted quantity is zero.
    """
    raise NotImplementedError("core logic: implement by hand")


def depth_slope(view: BookView, side: str, levels: int = 10) -> float:
    """Slope of cumulative quantity against price distance from the mid.

    Fit ``cumulative_qty = slope * |price - mid|`` by ordinary least squares
    through the origin over the top ``levels`` of ``side`` (``'bid'`` or
    ``'ask'``). A steep slope means depth builds quickly away from the touch --
    a resilient book. A flat one means a large order walks a long way.

    Returns ``nan`` if the side has fewer than two levels or the mid is
    undefined.
    """
    raise NotImplementedError("core logic: implement by hand")


def log_depth_ratio(view: BookView, levels: int = 10) -> float:
    """``log(B / A)`` over the top ``levels``.

    The unbounded sibling of :func:`order_book_imbalance`. Symmetric around zero
    and additive under quantity ratios, which makes it better behaved as a
    regression input than the bounded ratio, whose variance collapses near the
    extremes.

    Returns ``nan`` if either side sums to zero.
    """
    raise NotImplementedError("core logic: implement by hand")
