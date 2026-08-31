"""Evaluating signals against what happened next.

Separate from :mod:`l2tca.signals`, which computes factors from a book and makes
no claim about them. This package asks whether those numbers carry information,
which is a different question with different failure modes -- chiefly that it is
very easy to answer it accidentally yes.
"""

from l2tca.research.evaluate import (
    bucket_summary,
    forward_return_bps,
    information_coefficient,
    signals_wide,
)

__all__ = [
    "bucket_summary",
    "forward_return_bps",
    "information_coefficient",
    "signals_wide",
]
