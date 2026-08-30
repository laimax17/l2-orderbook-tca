"""Specification for :mod:`l2tca.book.order_book`, as executable tests.

These FAIL until the book is written. That is the point: this file is the
development target, and it is the answer key the docstrings deliberately
withhold. Run them with::

    uv run pytest -m core tests/test_order_book.py

Edge cases covered, in the order the project brief lists them:
  - a quantity of zero means the level is gone
  - a delete for a level the book does not hold
  - a crossed book (bid >= ask)
  - price precision when the price is a key
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from tests.factories import capture_book_frames, snapshot_frame, update_frame

from l2tca.book.order_book import OrderBook
from l2tca.book.types import Side

pytestmark = pytest.mark.core


def D(v: str) -> Decimal:
    return Decimal(v)


def loaded_book(depth: int = 4) -> OrderBook:
    book = OrderBook("BTC/USD", depth=depth)
    book.apply_snapshot(
        snapshot_frame(
            [("100.0", "10"), ("99.0", "20"), ("98.0", "30")],
            [("101.0", "5"), ("102.0", "15"), ("103.0", "25")],
        )
    )
    return book


# -- snapshots -------------------------------------------------------------


def test_snapshot_sorts_each_side_best_first() -> None:
    bids, asks = loaded_book().depth_levels(3)
    assert [b.price for b in bids] == [D("100.0"), D("99.0"), D("98.0")]
    assert [a.price for a in asks] == [D("101.0"), D("102.0"), D("103.0")]


def test_snapshot_replaces_rather_than_merges() -> None:
    """A snapshot is authoritative; anything held before it is stale by definition."""
    book = loaded_book()
    book.apply_snapshot(snapshot_frame([("50.0", "1")], [("51.0", "1")]))
    bids, asks = book.depth_levels(10)
    assert [b.price for b in bids] == [D("50.0")]
    assert [a.price for a in asks] == [D("51.0")]


def test_a_crossed_snapshot_is_rejected() -> None:
    with pytest.raises(ValueError):
        OrderBook("BTC/USD", 4).apply_snapshot(
            snapshot_frame([("101.0", "1")], [("100.0", "1")])
        )


def test_a_snapshot_with_a_non_positive_quantity_is_rejected() -> None:
    with pytest.raises(ValueError):
        OrderBook("BTC/USD", 4).apply_snapshot(snapshot_frame([("100.0", "0")], [("101.0", "1")]))


# -- incremental updates ---------------------------------------------------


def test_a_non_zero_quantity_replaces_and_never_accumulates() -> None:
    """The wire carries absolute resting quantity, not a delta."""
    book = loaded_book()
    book.apply_update(update_frame([("100.0", "3")], []))
    assert book.best_bid == (D("100.0"), D("3"))


def test_zero_quantity_removes_the_level() -> None:
    book = loaded_book()
    book.apply_update(update_frame([("100.0", "0")], []))
    assert book.best_bid.price == D("99.0")


def test_deleting_a_price_that_is_not_in_the_book_is_not_an_error() -> None:
    """It refers to a level below the depth window. Normal, not a fault."""
    book = loaded_book()
    before = book.depth_levels(10)
    book.apply_update(update_frame([("1.0", "0")], [("99999.0", "0")]))
    assert book.depth_levels(10) == before


def test_a_new_level_is_inserted_in_price_order() -> None:
    book = loaded_book()
    book.apply_update(update_frame([("99.5", "7")], []))
    bids, _ = book.depth_levels(4)
    assert [b.price for b in bids] == [D("100.0"), D("99.5"), D("99.0"), D("98.0")]


def test_each_side_is_trimmed_to_depth() -> None:
    """The exchange never announces a level falling out of the bottom of the window."""
    book = OrderBook("BTC/USD", depth=4)
    book.apply_snapshot(
        snapshot_frame(
            [(f"{100 - i}.0", "1") for i in range(4)],
            [(f"{101 + i}.0", "1") for i in range(4)],
        )
    )
    book.apply_update(update_frame([("96.0", "1"), ("95.0", "1")], []))
    bids, _ = book.depth_levels(100)
    assert len(bids) == 4
    assert [b.price for b in bids] == [D("100.0"), D("99.0"), D("98.0"), D("97.0")]


def test_an_update_that_would_cross_the_book_is_rejected() -> None:
    """A crossed book is never a market state the exchange published."""
    book = loaded_book()
    with pytest.raises(ValueError):
        book.apply_update(update_frame([("105.0", "1")], []))


def test_a_frame_that_moves_both_sides_is_applied() -> None:
    """One frame carries both sides. The book only has to be uncrossed after it.

    Kraken lifts the whole book in a single update: new bids that sit above the
    *old* best ask, alongside the new asks that clear them. Judging each level
    against the book as it stands mid-frame rejects a frame the exchange
    published, and no amount of retrying will make it acceptable.
    """
    book = OrderBook("BTC/USD", depth=10)
    book.apply_snapshot(snapshot_frame([("100.0", "1")], [("101.0", "1")]))

    book.apply_update(update_frame([("102.0", "1"), ("100.0", "0")],
                                   [("103.0", "1"), ("101.0", "0")]))

    assert book.best_bid == (D("102.0"), D("1"))
    assert book.best_ask == (D("103.0"), D("1"))


def test_an_update_applies_when_the_opposite_side_is_empty() -> None:
    """A one-sided book is a legal state, so an update to it must not raise."""
    book = OrderBook("BTC/USD", depth=10)
    book.apply_snapshot(snapshot_frame([("100.0", "1")], []))

    book.apply_update(update_frame([("99.0", "5")], []))

    bids, asks = book.depth_levels(10)
    assert [b.price for b in bids] == [D("100.0"), D("99.0")]
    assert asks == ()

    # ...and the same in the other direction.
    book.apply_update(update_frame([], [("101.0", "2")]))
    assert book.best_ask == (D("101.0"), D("2"))


def test_seq_counts_applied_frames() -> None:
    book = loaded_book()
    start = book.seq
    book.apply_update(update_frame([("100.0", "1")], []))
    book.apply_update(update_frame([], [("101.0", "1")]))
    assert book.seq == start + 2


def test_clear_empties_the_book() -> None:
    book = loaded_book()
    book.clear()
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.depth_levels(10) == ((), ())


# -- price precision -------------------------------------------------------


def test_trailing_zeros_do_not_create_a_second_level() -> None:
    """``45283.50`` and ``45283.5`` are the same price level, whatever the key type."""
    book = OrderBook("BTC/USD", depth=10)
    book.apply_snapshot(snapshot_frame([("45283.50", "1")], [("45284.0", "1")]))
    book.apply_update(update_frame([("45283.5", "7")], []))

    bids, _ = book.depth_levels(10)
    assert len(bids) == 1
    assert bids[0].qty == D("7")


def test_prices_survive_as_exact_decimals() -> None:
    """Binary float would make 0.1 + 0.2 a book bug rather than a display quirk."""
    book = OrderBook("BTC/USD", depth=10)
    book.apply_snapshot(snapshot_frame([("0.1", "1"), ("0.2", "1")], [("0.3", "1")]))
    bids, asks = book.depth_levels(10)
    assert isinstance(bids[0].price, Decimal)
    assert bids[1].price + bids[0].price == asks[0].price
    assert str(bids[0].price) == "0.2"


def test_a_sub_tick_price_is_its_own_level() -> None:
    book = OrderBook("BTC/USD", depth=10)
    book.apply_snapshot(snapshot_frame([("100.00000001", "1"), ("100.0", "2")], [("101.0", "1")]))
    bids, _ = book.depth_levels(10)
    assert [b.price for b in bids] == [D("100.00000001"), D("100.0")]


# -- reads -----------------------------------------------------------------


def test_top_of_book_accessors() -> None:
    book = loaded_book()
    assert book.best_bid == (D("100.0"), D("10"))
    assert book.best_ask == (D("101.0"), D("5"))
    assert book.mid == D("100.5")
    assert book.spread == D("1.0")


def test_top_of_book_on_an_empty_book_is_none() -> None:
    book = OrderBook("BTC/USD", 4)
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.mid is None
    assert book.spread is None


def test_mid_is_undefined_on_a_one_sided_book() -> None:
    book = OrderBook("BTC/USD", 4)
    book.apply_snapshot(snapshot_frame([("100.0", "1")], []))
    assert book.best_bid is not None
    assert book.mid is None


def test_depth_levels_returns_what_exists_when_the_side_is_thin() -> None:
    bids, asks = loaded_book().depth_levels(50)
    assert len(bids) == 3
    assert len(asks) == 3


def test_depth_levels_truncates_to_n_from_the_best_price() -> None:
    """Both sides are truncated from the touch, which is at opposite ends.

    ``bids`` descends and ``asks`` ascends, so "the best n" is the last n of one
    ordering and the first n of the other. Taking them from the same end of both
    yields the deepest bids and the deepest asks -- the exact opposite of what
    every caller wants.
    """
    book = OrderBook("BTC/USD", depth=20)
    book.apply_snapshot(
        snapshot_frame(
            [(f"{100 - i}.0", f"{i + 1}") for i in range(6)],
            [(f"{101 + i}.0", f"{i + 1}") for i in range(6)],
        )
    )

    bids, asks = book.depth_levels(3)
    assert [b.price for b in bids] == [D("100.0"), D("99.0"), D("98.0")]
    assert [a.price for a in asks] == [D("101.0"), D("102.0"), D("103.0")]
    # Quantities travel with their own price, not with the position.
    assert [b.qty for b in bids] == [D("1"), D("2"), D("3")]
    assert [a.qty for a in asks] == [D("1"), D("2"), D("3")]


def test_view_defaults_to_the_full_depth() -> None:
    """``view()`` with no argument is the common call on the per-frame path."""
    book = OrderBook("BTC/USD", depth=4)
    book.apply_snapshot(
        snapshot_frame(
            [(f"{100 - i}.0", "1") for i in range(4)],
            [(f"{101 + i}.0", "1") for i in range(4)],
        )
    )

    full = book.view()
    assert [b.price for b in full.bids] == [D("100.0"), D("99.0"), D("98.0"), D("97.0")]
    assert [a.price for a in full.asks] == [D("101.0"), D("102.0"), D("103.0"), D("104.0")]
    assert full.bids == book.view(4).bids


def test_view_is_an_immutable_copy() -> None:
    """Callers hold views while the live book keeps mutating."""
    book = loaded_book()
    snap = book.view(3, recv_ns=42)
    book.apply_update(update_frame([("100.0", "0")], []))
    assert snap.bids[0].price == D("100.0")
    assert snap.recv_ns == 42
    assert snap.seq == 1


def test_quantity_to_price_accumulates_to_the_limit() -> None:
    book = loaded_book()
    assert book.quantity_to_price(Side.ASK, D("102.0")) == D("20")  # 5 + 15
    assert book.quantity_to_price(Side.BID, D("99.0")) == D("30")  # 10 + 20
    assert book.quantity_to_price(Side.ASK, D("100.0")) == D("0")


# -- against a whole capture -----------------------------------------------


def test_invariants_hold_across_a_whole_capture(capture: Path) -> None:
    """Replay every frame and check the book never violates its own rules."""
    snapshots, updates = capture_book_frames(capture)
    book = OrderBook("BTC/USD", depth=100)
    book.apply_snapshot(snapshots[0])

    for update in updates:
        book.apply_update(update)
        bids, asks = book.depth_levels(100)

        assert len(bids) <= book.depth and len(asks) <= book.depth
        assert all(level.qty > 0 for level in bids + asks)
        assert [b.price for b in bids] == sorted({b.price for b in bids}, reverse=True)
        assert [a.price for a in asks] == sorted({a.price for a in asks})
        if bids and asks:
            assert bids[0].price < asks[0].price


def test_a_real_capture_replays_without_violating_invariants(sample_capture: Path) -> None:
    """Skips until a recorded sample is committed to tests/fixtures/."""
    snapshots, updates = capture_book_frames(sample_capture)
    assert snapshots, "a capture should open with a snapshot"

    book = OrderBook("BTC/USD", depth=100)
    book.apply_snapshot(snapshots[0])
    for update in updates:
        book.apply_update(update)

    bids, asks = book.depth_levels(100)
    assert bids and asks
    assert bids[0].price < asks[0].price
