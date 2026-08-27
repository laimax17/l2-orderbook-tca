"""Reconnect backoff.

Kept as a pure, injectable-RNG generator so the retry policy can be unit tested
without sleeping, without a socket, and without flaky timing assertions.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

__all__ = ["backoff_delays", "nth_delay_bound"]


def nth_delay_bound(
    attempt: int,
    *,
    initial: float,
    maximum: float,
    multiplier: float,
) -> float:
    """Upper bound on the delay before retry number ``attempt`` (1-based)."""
    if attempt < 1:
        raise ValueError("attempt is 1-based")
    return min(maximum, initial * multiplier ** (attempt - 1))


def backoff_delays(
    *,
    initial: float,
    maximum: float,
    multiplier: float,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> Iterator[float]:
    """Yield successive reconnect delays forever.

    Uses *full jitter* (``uniform(0, bound)``) rather than the bounded-exponential
    delay itself. With many clients reconnecting after a common outage, an
    unjittered schedule synchronises them into a thundering herd that keeps the
    endpoint down; full jitter spreads the retries flat across the window and is
    the variant that minimises both server load and client completion time in
    AWS's published comparison.

    Args:
        initial: Delay bound before the first retry, in seconds.
        maximum: Cap on the bound, in seconds.
        multiplier: Growth factor per attempt; must be > 1.
        jitter: When ``False``, yield the bound itself. Useful in tests.
        rng: Injected for determinism. Defaults to the module-level generator.
    """
    if initial <= 0:
        raise ValueError("initial must be positive")
    if maximum < initial:
        raise ValueError("maximum must be >= initial")
    if multiplier <= 1.0:
        raise ValueError("multiplier must be > 1")

    source = rng or random
    attempt = 1
    while True:
        bound = nth_delay_bound(attempt, initial=initial, maximum=maximum, multiplier=multiplier)
        yield source.uniform(0.0, bound) if jitter else bound
        attempt += 1
