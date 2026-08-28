"""Plots over stored Parquet, and over a benchmark report.

matplotlib is an optional dependency (``pip install 'l2tca[plot]'``): a machine
that only captures data should not have to install a plotting stack. Every
entry point imports it lazily and fails with a clear message if it is absent.

Figures are returned rather than shown, so the caller decides between
``fig.savefig(...)`` and an interactive backend, and so the functions are
testable headlessly.

Note which table each one reads. ``depth`` and ``spread`` read the ``snapshot``
table, which is written from reconstructed book views -- so they have no input
until :mod:`l2tca.book` is implemented. ``latency`` reads a benchmark report and
works today.
"""

from l2tca.plot.depth import plot_depth_snapshot
from l2tca.plot.latency import plot_latency_histogram
from l2tca.plot.spread import plot_spread_series

__all__ = ["plot_depth_snapshot", "plot_latency_histogram", "plot_spread_series"]
