"""Specification for :mod:`l2tca.book.sequence`, as executable tests.

FAILS until the state machine is written. These pin the *contract* the rest of
the system needs -- when the book may be trusted, what happens to frames that
arrive during a resync -- not a particular set of state labels. How the machine
is structured internally is yours; how it behaves at these boundaries is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.factories import capture_book_frames, levels, snapshot_frame, update_frame

from l2tca.book.sequence import SequenceTracker, verify_checksum

pytestmark = pytest.mark.core


SNAP = snapshot_frame([("100.0", "1"), ("99.0", "2")], [("101.0", "1"), ("102.0", "2")])


def tracked() -> SequenceTracker:
    tracker = SequenceTracker("BTC/USD", depth=100)
    tracker.on_snapshot(SNAP)
    return tracker


# -- trust ------------------------------------------------------------------


def test_a_fresh_tracker_has_nothing_to_trust() -> None:
    tracker = SequenceTracker("BTC/USD", depth=100)
    assert tracker.needs_resync() is True


def test_an_update_before_any_snapshot_must_not_be_applied() -> None:
    tracker = SequenceTracker("BTC/USD", depth=100)
    assert tracker.on_update(update_frame([("100.0", "5")], [])) is False


def test_a_snapshot_makes_the_book_trustworthy() -> None:
    tracker = tracked()
    assert tracker.needs_resync() is False
    assert tracker.on_update(update_frame([("100.0", "5")], [])) is True


def test_a_disconnect_invalidates_the_book() -> None:
    """Whatever was held is stale the moment the transport goes away."""
    tracker = tracked()
    tracker.on_disconnect()
    assert tracker.needs_resync() is True
    assert tracker.on_update(update_frame([("100.0", "5")], [])) is False


def test_a_replacement_snapshot_restores_trust() -> None:
    tracker = tracked()
    tracker.on_disconnect()
    tracker.on_snapshot(SNAP)
    assert tracker.needs_resync() is False
    assert tracker.on_update(update_frame([("100.0", "5")], [])) is True


def test_a_failed_integrity_check_forces_a_resync() -> None:
    """A checksum mismatch means the local book has silently diverged."""
    tracker = tracked()
    assert tracker.on_update(update_frame([("100.0", "5")], [], checksum=1)) is False
    assert tracker.needs_resync() is True


# -- buffering during a resync ---------------------------------------------


def test_buffered_updates_drain_in_arrival_order() -> None:
    tracker = tracked()
    tracker.on_disconnect()

    frames = [update_frame([(f"{100 - i}.0", "1")], []) for i in range(3)]
    for frame in frames:
        tracker.buffer(frame)

    drained = tracker.drain()
    assert [f.bids[0].price for f in drained] == [f.bids[0].price for f in frames]


def test_draining_empties_the_buffer() -> None:
    tracker = tracked()
    tracker.on_disconnect()
    tracker.buffer(update_frame([("100.0", "1")], []))
    assert len(tracker.drain()) == 1
    assert tracker.drain() == []


def test_an_empty_buffer_drains_to_nothing() -> None:
    assert tracked().drain() == []


def test_a_snapshot_arriving_mid_resync_supersedes_the_buffer() -> None:
    """The snapshot already reflects those updates; replaying them double-counts."""
    tracker = tracked()
    tracker.on_disconnect()
    tracker.buffer(update_frame([("100.0", "1")], []))
    tracker.on_snapshot(SNAP)
    assert tracker.drain() == []
    assert tracker.needs_resync() is False


# -- checksum ---------------------------------------------------------------


def test_checksum_verification_is_deterministic() -> None:
    bids = levels([("100.0", "1"), ("99.0", "2")])
    asks = levels([("101.0", "1"), ("102.0", "2")])
    first = verify_checksum(bids, asks, 12345, 1, 8)
    assert first is verify_checksum(bids, asks, 12345, 1, 8)
    assert isinstance(first, bool)


def test_two_different_expectations_cannot_both_verify() -> None:
    bids = levels([("100.0", "1")])
    asks = levels([("101.0", "1")])
    a = verify_checksum(bids, asks, 1, 1, 8)
    b = verify_checksum(bids, asks, 2, 1, 8)
    assert not (a and b)


def test_the_checksum_depends_on_the_book() -> None:
    asks = levels([("101.0", "1")])
    one = levels([("100.0", "1")])
    other = levels([("100.0", "2")])
    # Whatever the true checksum of `one` is, `other` must not also match it.
    assert not all(
        verify_checksum(side, asks, expected, 1, 8)
        for side, expected in ((one, 7), (other, 7))
    )


def test_a_clean_capture_never_forces_a_resync(sample_capture: Path) -> None:
    """The strongest check available on the tracker as a whole.

    The committed capture was recorded with no reconnects and every one of its
    checksums verifies, so a correct tracker applies every frame and never asks
    for a fresh snapshot. A rejection here is a real defect, and it could be in
    any of three places -- the tracker, the checksum, or the book underneath --
    which is exactly why it is worth running.

    Unlike the unit tests above, this one cannot be satisfied by a tracker that
    guesses: rejecting nothing and rejecting everything both fail it, the first
    only if the frames genuinely diverge.
    """
    snapshots, updates = capture_book_frames(sample_capture)
    tracker = SequenceTracker("BTC/USD", depth=100)
    tracker.on_snapshot(snapshots[0])

    rejected = [u for u in updates if not tracker.on_update(u)]
    assert not rejected, f"{len(rejected)} of {len(updates)} frames were rejected"
    assert tracker.needs_resync() is False


def test_checksums_verify_against_a_real_capture(sample_capture: Path) -> None:
    """The authoritative test. Skips until a recorded sample is committed.

    Synthetic frames carry no checksum on purpose -- see
    :mod:`l2tca.feed.synthetic` -- so this is the only place the checksum
    implementation is genuinely proven.
    """
    snapshots, updates = capture_book_frames(sample_capture)
    checked = [u for u in updates if u.checksum is not None]
    if not checked:
        pytest.skip("committed sample carries no checksums")

    from l2tca.book.order_book import OrderBook

    book = OrderBook("BTC/USD", depth=100)
    book.apply_snapshot(snapshots[0])
    verified = 0
    for update in checked[:200]:
        book.apply_update(update)
        bids, asks = book.depth_levels(10)
        # Precisions come from Kraken's instrument data, not from book frames.
        assert verify_checksum(bids, asks, update.checksum, 1, 8)
        verified += 1
    assert verified > 0
