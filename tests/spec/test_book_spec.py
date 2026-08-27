"""Executable specification for :mod:`l2tca.book.l2_book`.

Every test here describes behaviour the reconstruction must have. They are
marked so the suite stays green while the book is a stub; see
``tests/spec/marks.py`` for exactly what the marker does and does not tolerate.
"""

from __future__ import annotations

import zlib
from decimal import Decimal

import pytest
from tests.spec.marks import unimplemented

from l2tca.book.l2_book import L2Book, kraken_book_checksum
from l2tca.feed.messages import BookLevel, BookSnapshot, BookUpdate


def D(v: str) -> Decimal:
    return Decimal(v)


def snapshot(bids: list[tuple[str, str]], asks: list[tuple[str, str]], **kw) -> BookSnapshot:
    return BookSnapshot(
        symbol=kw.get("symbol", "BTC/USD"),
        bids=tuple(BookLevel(D(p), D(q)) for p, q in bids),
        asks=tuple(BookLevel(D(p), D(q)) for p, q in asks),
        checksum=kw.get("checksum"),
        exchange_ts_ns=kw.get("exchange_ts_ns"),
    )


def update(bids: list[tuple[str, str]], asks: list[tuple[str, str]], **kw) -> BookUpdate:
    return BookUpdate(
        symbol=kw.get("symbol", "BTC/USD"),
        bids=tuple(BookLevel(D(p), D(q)) for p, q in bids),
        asks=tuple(BookLevel(D(p), D(q)) for p, q in asks),
        checksum=kw.get("checksum"),
        exchange_ts_ns=kw.get("exchange_ts_ns"),
    )


@pytest.fixture
def book() -> L2Book:
    return L2Book("BTC/USD", depth=4)


@pytest.fixture
def loaded(book: L2Book) -> L2Book:
    book.apply_snapshot(
        snapshot(
            [("100.0", "10"), ("99.0", "20"), ("98.0", "30")],
            [("101.0", "5"), ("102.0", "15"), ("103.0", "25")],
        )
    )
    return book


# -- snapshots -------------------------------------------------------------


@unimplemented
def test_snapshot_sorts_each_side_best_first(loaded: L2Book) -> None:
    bids, asks = loaded.depth_levels(3)
    assert [b.price for b in bids] == [D("100.0"), D("99.0"), D("98.0")]
    assert [a.price for a in asks] == [D("101.0"), D("102.0"), D("103.0")]


@unimplemented
def test_snapshot_replaces_rather_than_merges(loaded: L2Book) -> None:
    """A snapshot is authoritative; anything held before it is stale by definition."""
    loaded.apply_snapshot(snapshot([("50.0", "1")], [("51.0", "1")]))
    bids, asks = loaded.depth_levels(10)
    assert [b.price for b in bids] == [D("50.0")]
    assert [a.price for a in asks] == [D("51.0")]


@unimplemented
def test_a_crossed_snapshot_is_rejected(book: L2Book) -> None:
    with pytest.raises(ValueError):
        book.apply_snapshot(snapshot([("101.0", "1")], [("100.0", "1")]))


@unimplemented
def test_a_snapshot_with_a_non_positive_quantity_is_rejected(book: L2Book) -> None:
    with pytest.raises(ValueError):
        book.apply_snapshot(snapshot([("100.0", "0")], [("101.0", "1")]))


# -- incremental updates ---------------------------------------------------


@unimplemented
def test_update_replaces_the_quantity_and_never_adds_to_it(loaded: L2Book) -> None:
    """Kraken sends absolute resting quantity. Adding is the classic wrong version."""
    loaded.apply_update(update([("100.0", "3")], []))
    assert loaded.best_bid == (D("100.0"), D("3"))


@unimplemented
def test_zero_quantity_removes_the_level(loaded: L2Book) -> None:
    loaded.apply_update(update([("100.0", "0")], []))
    assert loaded.best_bid.price == D("99.0")


@unimplemented
def test_deleting_a_price_that_is_not_in_the_book_is_not_an_error(loaded: L2Book) -> None:
    """It refers to a level that fell below the depth window. Normal, not a fault."""
    before = loaded.depth_levels(10)
    loaded.apply_update(update([("1.0", "0")], []))
    assert loaded.depth_levels(10) == before


@unimplemented
def test_a_new_level_is_inserted_in_price_order(loaded: L2Book) -> None:
    loaded.apply_update(update([("99.5", "7")], []))
    bids, _ = loaded.depth_levels(4)
    assert [b.price for b in bids] == [D("100.0"), D("99.5"), D("99.0"), D("98.0")]


@unimplemented
def test_each_side_is_trimmed_to_depth(book: L2Book) -> None:
    """The exchange never announces a level falling out of the bottom of the window."""
    book.apply_snapshot(
        snapshot(
            [(f"{100 - i}.0", "1") for i in range(4)],
            [(f"{101 + i}.0", "1") for i in range(4)],
        )
    )
    book.apply_update(update([("96.0", "1"), ("95.0", "1")], []))
    bids, _ = book.depth_levels(100)
    assert len(bids) == 4
    assert [b.price for b in bids] == [D("100.0"), D("99.0"), D("98.0"), D("97.0")]


@unimplemented
def test_seq_counts_applied_frames(loaded: L2Book) -> None:
    start = loaded.seq
    loaded.apply_update(update([("100.0", "1")], []))
    loaded.apply_update(update([], [("101.0", "1")]))
    assert loaded.seq == start + 2


@unimplemented
def test_clear_empties_the_book(loaded: L2Book) -> None:
    loaded.clear()
    assert loaded.best_bid is None
    assert loaded.best_ask is None
    assert loaded.depth_levels(10) == ((), ())


# -- reads -----------------------------------------------------------------


@unimplemented
def test_top_of_book_accessors(loaded: L2Book) -> None:
    assert loaded.best_bid == (D("100.0"), D("10"))
    assert loaded.best_ask == (D("101.0"), D("5"))
    assert loaded.mid == D("100.5")
    assert loaded.spread == D("1.0")


@unimplemented
def test_top_of_book_on_an_empty_book_is_none(book: L2Book) -> None:
    assert book.best_bid is None
    assert book.mid is None
    assert book.spread is None


@unimplemented
def test_depth_levels_returns_what_exists_when_the_side_is_thin(loaded: L2Book) -> None:
    bids, asks = loaded.depth_levels(50)
    assert len(bids) == 3 and len(asks) == 3


@unimplemented
def test_view_is_an_immutable_copy(loaded: L2Book) -> None:
    """Callers hold views while the live book keeps mutating."""
    snap = loaded.view(3, recv_ns=42)
    loaded.apply_update(update([("100.0", "0")], []))
    assert snap.bids[0].price == D("100.0")
    assert snap.recv_ns == 42
    assert not snap.is_crossed


@unimplemented
def test_notional_to_price_accumulates_to_the_limit(loaded: L2Book) -> None:
    assert loaded.notional_to_price("ask", D("102.0")) == D("20")  # 5 + 15
    assert loaded.notional_to_price("bid", D("99.0")) == D("30")  # 10 + 20
    assert loaded.notional_to_price("ask", D("100.0")) == D("0")


# -- checksum --------------------------------------------------------------


@unimplemented
def test_checksum_follows_krakens_string_construction() -> None:
    """Asks first, then bids; strip the decimal point, then leading zeros; CRC32."""
    from l2tca.book.base import Level

    bids = (Level(D("0.5"), D("1.25")),)
    asks = (Level(D("1.5"), D("2.5")),)
    expected = zlib.crc32(b"150000250000000" + b"50000125000000")
    assert kraken_book_checksum(bids, asks, price_precision=5, qty_precision=8) == expected


@unimplemented
def test_checksum_uses_at_most_the_top_ten_levels() -> None:
    from l2tca.book.base import Level

    deep_bids = tuple(Level(D(f"{100 - i}"), D("1")) for i in range(20))
    deep_asks = tuple(Level(D(f"{101 + i}"), D("1")) for i in range(20))
    assert kraken_book_checksum(
        deep_bids, deep_asks, 1, 1
    ) == kraken_book_checksum(deep_bids[:10], deep_asks[:10], 1, 1)


@unimplemented
def test_checksum_does_not_use_exponent_notation() -> None:
    """``Decimal.__str__`` switches to exponents for small values; the wire format never does."""
    from l2tca.book.base import Level

    tiny = (Level(D("0.0000001"), D("0.00000001")),)
    other = (Level(D("1E-7"), D("1E-8")),)
    assert kraken_book_checksum(tiny, tiny, 8, 8) == kraken_book_checksum(other, other, 8, 8)


@unimplemented
def test_checksum_is_an_unsigned_32_bit_value() -> None:
    from l2tca.book.base import Level

    value = kraken_book_checksum((Level(D("1"), D("1")),), (Level(D("2"), D("1")),), 1, 1)
    assert 0 <= value <= 0xFFFF_FFFF


@unimplemented
def test_a_checksum_mismatch_is_counted(loaded: L2Book) -> None:
    """A mismatch means the local book has silently diverged. It must be visible."""
    loaded.apply_update(update([("100.0", "1")], [], checksum=1))
    assert loaded.checksum_failures == 1
