"""Execution cost on observed trades, pinned on a book small enough to check by hand.

The book below is chosen so the mid is exactly 100 and one currency unit of cost
is exactly 100 bps of a full (doubled) spread:

    t = 0s    mid 100.00    quoted 10 bps
    t = 5s    mid 101.00
    t = 9s    mid  99.00
"""

from __future__ import annotations

import polars as pl
import pytest

from l2tca.research import execution_costs, summarise_costs

SECOND = 1_000_000_000


def book() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "recv_ns": [0, 5 * SECOND, 9 * SECOND],
            "mid": [100.0, 101.0, 99.0],
            "quoted_spread_bps": [10.0, 10.0, 10.0],
        }
    )


def trades(rows: list[tuple[int, str, float, float]], *, frame_type: str = "update"):
    return pl.DataFrame(
        {
            "recv_ns": [r[0] for r in rows],
            "side": [r[1] for r in rows],
            "price": [r[2] for r in rows],
            "qty": [r[3] for r in rows],
            "frame_type": [frame_type] * len(rows),
        }
    )


def test_a_buy_above_the_mid_paid_a_positive_spread() -> None:
    """0.50 above a mid of 100, doubled, is 100 bps."""
    out = execution_costs(trades([(0, "buy", 100.50, 1.0)]), book(), horizon_ns=5 * SECOND)
    assert out["effective_bps"][0] == pytest.approx(100.0)


def test_a_sell_below_the_mid_also_paid_a_positive_spread() -> None:
    """Sign convention: positive is a cost to the taker on both sides."""
    out = execution_costs(trades([(0, "sell", 99.50, 1.0)]), book(), horizon_ns=5 * SECOND)
    assert out["effective_bps"][0] == pytest.approx(100.0)


def test_the_decomposition_is_exact() -> None:
    """effective == realized + impact, by construction, on every row."""
    out = execution_costs(
        trades([(0, "buy", 100.50, 1.0), (0, "sell", 99.50, 2.0)]),
        book(),
        horizon_ns=5 * SECOND,
    )
    residual = out["effective_bps"] - (out["realized_bps"] + out["impact_bps"])
    assert max(abs(v) for v in residual) < 1e-9


def test_a_buy_that_the_market_ran_away_from_earned_the_seller_nothing() -> None:
    """Mid 100 -> 101 in the horizon. The buyer paid 0.50 and the market gave 1.00 back.

    Realized is negative: whoever sold at 100.50 was picked off. Impact carries
    the whole cost, which is what adverse selection looks like.
    """
    out = execution_costs(trades([(0, "buy", 100.50, 1.0)]), book(), horizon_ns=5 * SECOND)
    assert out["effective_bps"][0] == pytest.approx(100.0)
    assert out["realized_bps"][0] == pytest.approx(-100.0)  # 2 * (100.50 - 101) / 100
    assert out["impact_bps"][0] == pytest.approx(200.0)  # 2 * (101 - 100) / 100


def test_a_trade_the_market_reverted_after_was_genuinely_earned() -> None:
    """A taker buys at 101.50 against a mid of 101, then the mid falls to 99.

    ``side`` is the *aggressor's*. Realized spread is about the other party: the
    resting seller filled at 101.50 and could buy back at 99, so they kept the
    spread and more. Impact is negative, because the market moved in the resting
    side's favour rather than against it.
    """
    out = execution_costs(
        trades([(5 * SECOND, "buy", 101.50, 1.0)]), book(), horizon_ns=4 * SECOND
    )
    assert out["effective_bps"][0] == pytest.approx(2 * (101.50 - 101.0) / 101.0 * 10_000)
    assert out["realized_bps"][0] > out["effective_bps"][0]
    assert out["impact_bps"][0] < 0


def test_an_informed_taker_leaves_the_resting_side_worse_than_the_spread() -> None:
    """The mirror image, and the reason the decomposition exists.

    A taker sells at 100.50 against a mid of 101 and the mid then falls to 99.
    The taker paid a positive effective spread, and the resting buyer still lost:
    they bought at 100.50 and the market left them at 99. That is adverse
    selection, and it is invisible in the effective spread alone.
    """
    out = execution_costs(
        trades([(5 * SECOND, "sell", 100.50, 1.0)]), book(), horizon_ns=4 * SECOND
    )
    assert out["effective_bps"][0] > 0  # the taker paid, on the face of it
    assert out["realized_bps"][0] < 0  # and the resting side still lost
    assert out["impact_bps"][0] > out["effective_bps"][0]


def test_the_backfill_is_dropped() -> None:
    """Those prints predate the connection and all share one arrival stamp."""
    with pytest.raises(ValueError, match="no live trades"):
        execution_costs(trades([(0, "buy", 100.5, 1.0)], frame_type="snapshot"), book())

    mixed = pl.concat(
        [trades([(0, "buy", 100.5, 1.0)], frame_type="snapshot"), trades([(0, "buy", 100.5, 2.0)])]
    )
    assert execution_costs(mixed, book(), horizon_ns=5 * SECOND).height == 1


def test_a_trade_before_any_book_state_is_dropped() -> None:
    """Reaching forward to a book that had not arrived flatters every number."""
    out = execution_costs(trades([(-1, "buy", 100.5, 1.0)]), book(), horizon_ns=5 * SECOND)
    assert out.is_empty()


def test_no_horizon_leaves_realized_null_but_keeps_effective() -> None:
    """Effective needs only the present; realized needs a future the sample may not have."""
    out = execution_costs(trades([(9 * SECOND, "buy", 99.5, 1.0)]), book(), horizon_ns=5 * SECOND)
    assert out["effective_bps"][0] is not None
    assert out["realized_bps"][0] is None


def test_the_summary_is_size_weighted() -> None:
    """A venue's prints are mostly tiny; an unweighted mean describes the tiny ones."""
    out = execution_costs(
        trades([(0, "buy", 100.10, 0.01), (0, "buy", 101.00, 100.0)]),
        book(),
        horizon_ns=5 * SECOND,
    )
    summary = summarise_costs(out)
    small, large = out["effective_bps"][0], out["effective_bps"][1]
    weighted = summary["effective_bps_vw"][0]
    assert abs(weighted - large) < abs(weighted - small)
    assert summary["trades"][0] == 2


def test_the_summary_splits_improvement_from_walking_the_book() -> None:
    quoted = 10.0  # bps, so half-spread of 5 bps either side of a mid of 100
    inside = 100.02  # 4 bps doubled -> under the quoted spread
    through = 100.50  # 100 bps -> well through it
    out = execution_costs(
        trades([(0, "buy", inside, 1.0), (0, "buy", through, 1.0)]),
        book(),
        horizon_ns=5 * SECOND,
    )
    assert out["quoted_spread_bps"][0] == quoted
    summary = summarise_costs(out)
    assert 0 < summary["price_improvement_share"][0] < 1
    assert 0 < summary["through_touch_share"][0] < 1
    assert summary["price_improvement_share"][0] + summary["through_touch_share"][0] == 1.0


def test_summarising_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="no trades"):
        summarise_costs(pl.DataFrame())


# -- against the real capture ----------------------------------------------


@pytest.mark.core
def test_a_trade_at_the_touch_pays_exactly_the_quoted_spread() -> None:
    """The identity that checks the whole chain at once, on a real book.

    A taker who lifts the offer pays ``A - M``, and ``M`` is midway between
    ``A`` and ``B``, so ``2 * (A - M) == A - B``: the effective spread of a
    trade at the touch *is* the quoted spread, exactly. Any error in the
    alignment, the sign, the doubling or the basis-point scaling breaks it.

    Run against the committed capture rather than a constructed book, so the
    join is exercised on real, irregular arrival times.
    """
    from pathlib import Path

    from l2tca.io.derive import iter_book_views, signal_rows
    from l2tca.research import signals_wide

    views = [v for _m, v in iter_book_views(Path("tests/fixtures/sample.jsonl.gz"), limit=800)]
    views = [v for v in views if v.bids and v.asks]
    assert len(views) > 100

    rows = [row for v in views for row in signal_rows(v)]
    book = signals_wide(pl.DataFrame(rows))

    # One taker buy at the offer and one taker sell at the bid, per view.
    made = []
    for view in views:
        made.append((view.recv_ns, "buy", float(view.asks[0].price), 1.0))
        made.append((view.recv_ns, "sell", float(view.bids[0].price), 1.0))
    at_touch = trades(made)

    costs = execution_costs(at_touch, book, horizon_ns=SECOND)
    assert costs.height == len(made)
    residual = costs["effective_bps"] - costs["quoted_spread_bps"]
    assert max(abs(v) for v in residual) < 1e-6, "a touch trade must pay the quoted spread"


@pytest.mark.core
def test_trading_inside_the_touch_costs_less_than_quoted() -> None:
    """The other direction: a fill at the mid pays nothing, and the summary sees it."""
    from pathlib import Path

    from l2tca.io.derive import iter_book_views, signal_rows
    from l2tca.research import signals_wide

    views = [v for _m, v in iter_book_views(Path("tests/fixtures/sample.jsonl.gz"), limit=400)]
    views = [v for v in views if v.bids and v.asks]
    book = signals_wide(pl.DataFrame([row for v in views for row in signal_rows(v)]))

    at_mid = trades(
        [
            (v.recv_ns, "buy", (float(v.bids[0].price) + float(v.asks[0].price)) / 2, 1.0)
            for v in views
        ]
    )
    costs = execution_costs(at_mid, book, horizon_ns=SECOND)
    assert max(abs(v) for v in costs["effective_bps"]) < 1e-6
    assert summarise_costs(costs)["price_improvement_share"][0] == 1.0
