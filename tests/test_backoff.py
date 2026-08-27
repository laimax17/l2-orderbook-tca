from __future__ import annotations

import random
from itertools import islice

import pytest

from l2tca.feed.backoff import backoff_delays, nth_delay_bound


def test_bounds_grow_geometrically_then_clamp() -> None:
    bounds = [nth_delay_bound(n, initial=0.5, maximum=8.0, multiplier=2.0) for n in range(1, 7)]
    assert bounds == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]


def test_unjittered_delays_follow_the_bounds() -> None:
    delays = list(
        islice(backoff_delays(initial=1.0, maximum=4.0, multiplier=2.0, jitter=False), 4)
    )
    assert delays == [1.0, 2.0, 4.0, 4.0]


def test_full_jitter_stays_within_the_bound_and_is_seed_reproducible() -> None:
    def run() -> list[float]:
        return list(
            islice(
                backoff_delays(
                    initial=0.5, maximum=8.0, multiplier=2.0, rng=random.Random(1234)
                ),
                20,
            )
        )

    first = run()
    assert first == run(), "seeded backoff must be reproducible"
    for n, delay in enumerate(first, start=1):
        assert 0.0 <= delay <= nth_delay_bound(n, initial=0.5, maximum=8.0, multiplier=2.0)


def test_full_jitter_actually_spreads_the_retries() -> None:
    """The whole point of jitter: not every client waking at the same instant."""
    delays = list(
        islice(backoff_delays(initial=4.0, maximum=4.0, multiplier=2.0, rng=random.Random(7)), 50)
    )
    assert len(set(delays)) > 40
    assert min(delays) < 1.0 < max(delays)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial": 0.0, "maximum": 1.0, "multiplier": 2.0},
        {"initial": 2.0, "maximum": 1.0, "multiplier": 2.0},
        {"initial": 1.0, "maximum": 2.0, "multiplier": 1.0},
    ],
)
def test_invalid_parameters_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        next(backoff_delays(**kwargs))


def test_attempt_is_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        nth_delay_bound(0, initial=1.0, maximum=2.0, multiplier=2.0)
