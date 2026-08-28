"""Lazy matplotlib import, shared by the plot modules."""

from __future__ import annotations

from typing import Any

__all__ = ["pyplot"]


def pyplot() -> Any:
    """Return ``matplotlib.pyplot``, or raise with an actionable message."""
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError(
            "plotting needs matplotlib, which is an optional dependency. "
            "Install it with: pip install 'l2tca[plot]'  (or: uv sync --all-extras)"
        ) from exc

    # Chosen before pyplot is imported: these functions return figures rather
    # than showing them, and a GUI backend would fail on a headless capture host.
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    return plt
