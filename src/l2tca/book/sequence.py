"""Integrity and resynchronisation state machine. CORE LOGIC -- NOT IMPLEMENTED.

# ---------------------------------------------------------------------------
# States this machine needs (what each means, and how it ends, is yours to
# define -- this is a checklist, not a diagram):
#
#   - disconnected      no socket
#   - awaiting_snapshot subscribed, no authoritative book yet
#   - live              book is trusted, updates applied directly
#   - resyncing         book is known bad, a replacement snapshot is pending
#
# Conditions that must cause *some* transition (which one, and to where, is the
# design work -- do not assume the list below maps one-to-one onto the states
# above, and do not assume every condition moves the machine):
#
#   - the connection opened
#   - the connection dropped
#   - the subscription was acknowledged
#   - a snapshot frame arrived
#   - an update frame arrived while no book is held
#   - an update frame arrived that the book could not apply cleanly
#   - the integrity check failed on an applied frame
#   - the integrity check failed repeatedly
#   - a resubscribe was requested and its snapshot has not arrived yet
#   - frames continued to arrive while a resync was in flight
# ---------------------------------------------------------------------------

Why this module exists separately from :mod:`l2tca.book.order_book`: the book
answers "what is the state of the market", this answers "should I believe it".
Mixing the two produces a book that keeps serving numbers it has no reason to
trust, which is the worst of the available failure modes.

The Kraken ``checksum`` field
-----------------------------
Every ``book`` frame on Kraken's WebSocket v2 may carry an integer ``checksum``
(exposed as :attr:`BookUpdate.checksum`). It is the exchange's own statement of
what the top of the book should look like *after* the receiver has applied that
frame: the exchange computes it over its copy, the receiver computes it over its
copy, and a mismatch means the two books have diverged. Because it is
recomputed on every frame, divergence is caught within one message rather than
whenever someone next looks at the data.

It is the single most valuable integrity signal available here, and the only one
the exchange gives for free. Note what it does *not* cover: it is derived from a
bounded number of levels at the top of each side, so a book that is correct at
the touch and wrong further down passes.

Questions the verification has to answer:
  - How many levels, from which side first, and in what order?
  - How is each number rendered into the string that gets hashed? Kraken's
    docs specify this exactly; the rendering is where implementations go wrong.
  - The rendering depends on per-pair price and quantity precisions. Those are
    not present in book frames -- where do they come from, and what happens to
    every checksum if they are wrong?
  - Which hash, over which encoding, and what is the numeric type of the result?
  - What should happen on a single mismatch? On several in a row? Is continuing
    to serve a diverged book better or worse than a gap in coverage?
"""

from __future__ import annotations

from collections.abc import Iterable

from l2tca.book.types import Level
from l2tca.feed.messages import BookSnapshot, BookUpdate
from enum import StrEnum

__all__ = ["SequenceTracker", "verify_checksum"]

# No state type is declared here. Settle the states from the checklist above
# first; the right shape for them (enum, plain strings, something with data
# attached) falls out of that and not before.
#   - disconnected      no socket
#   - awaiting_snapshot subscribed, no authoritative book yet
#   - live              book is trusted, updates applied directly
#   - resyncing         book is known bad, a replacement snapshot is pending

class State(StrEnum):
    DISCONNECTED = "disconnected"
    AWAIT_SNAPSHOT = 'await_snapshot'
    LIVE = 'live'
    RESYNCING = 'resyncing'


class SequenceTracker:
    """Decides whether the book can be trusted, and drives resynchronisation.

    Kraken's v2 ``book`` channel does **not** carry a per-frame sequence number
    the way some venues do -- there is no monotonically increasing update id to
    compare against. Establish what plays that role here before writing anything
    else in this class, because every method below depends on the answer.
    """

    def __init__(self, symbol: str, depth: int = 100) -> None:
        self.symbol = symbol
        self.depth = depth

    def on_snapshot(self, snapshot: BookSnapshot) -> None:
        """Handle an arriving snapshot.

        Questions:
          - A snapshot can arrive when one was expected, and when one was not.
            Are those the same case?
          - What happens to anything buffered while it was in flight?
        """
        raise NotImplementedError("core logic: implement by hand")

    def on_update(self, update: BookUpdate) -> bool:
        """Handle an arriving update. Returns whether the caller should apply it.

        Questions:
          - What are the possible answers here besides "apply" and "drop"?
          - What does the caller need to know that a bool cannot carry?
        """
        raise NotImplementedError("core logic: implement by hand")

    def on_disconnect(self) -> None:
        """Handle the transport going away."""
        raise NotImplementedError("core logic: implement by hand")

    def needs_resync(self) -> bool:
        """Whether a fresh snapshot should be requested now.

        Question: who acts on this -- the feed client, the book, or something
        that owns both? What does that imply about where this class sits?
        """
        raise NotImplementedError("core logic: implement by hand")

    def buffer(self, update: BookUpdate) -> None:
        """Hold an update that arrived while a resync was in flight.

        Questions:
          - Is the buffer bounded? What happens when it fills?
          - When the snapshot lands, which buffered frames are still relevant?
            What decides that, given the note about sequence numbers above?
        """
        raise NotImplementedError("core logic: implement by hand")

    def drain(self) -> list[BookUpdate]:
        """Return the buffered updates that should now be applied, in order."""
        raise NotImplementedError("core logic: implement by hand")


def verify_checksum(
    bids: Iterable[Level],
    asks: Iterable[Level],
    expected: int,
    price_precision: int,
    qty_precision: int,
) -> bool:
    """Recompute Kraken's book checksum and compare it to ``expected``.

    See the module docstring for what the field is and the questions this has to
    answer. Kraken's API documentation specifies the construction exactly --
    read it there rather than inferring it from a capture.
    """
    raise NotImplementedError("core logic: implement by hand")
