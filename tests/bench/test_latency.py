"""Percentile and histogram maths."""

from __future__ import annotations

import gc
import math

import pytest

from l2tca.bench.histogram import histogram
from l2tca.bench.latency import LatencyRecorder, percentile_ns, timed


def test_percentiles_use_nearest_rank_so_they_are_observed_values() -> None:
    samples = list(range(1, 101))
    assert percentile_ns(samples, 50) == 50
    assert percentile_ns(samples, 99) == 99
    assert percentile_ns(samples, 100) == 100
    assert percentile_ns(samples, 1) == 1
    # No interpolation: every reported value actually occurred.
    assert percentile_ns([10, 20], 75) == 20


def test_percentile_edge_cases() -> None:
    assert math.isnan(percentile_ns([], 50))
    assert percentile_ns([42], 99.9) == 42
    with pytest.raises(ValueError):
        percentile_ns([1], 0)
    with pytest.raises(ValueError):
        percentile_ns([1], 101)


def test_recorder_summarises_samples() -> None:
    recorder = LatencyRecorder("stage")
    for value in [10, 20, 30, 40, 1000]:
        recorder.record(value)

    stats = recorder.stats()
    assert stats.count == 5
    assert stats.min_ns == 10
    assert stats.max_ns == 1000
    assert stats.mean_ns == 220
    assert stats.p50_ns == 30
    # The tail is why every sample is kept: the mean hides the 1000.
    assert stats.percentiles[99.0] == 1000


def test_empty_recorder_reports_nan_not_zero() -> None:
    stats = LatencyRecorder("empty").stats()
    assert stats.count == 0
    assert math.isnan(stats.p50_ns)
    assert math.isnan(stats.mean_ns)
    assert stats.hist.total == 0


def test_measure_times_calls_and_counts_but_does_not_time_failures() -> None:
    recorder = LatencyRecorder("stage")
    with recorder.measure():
        sum(range(1000))
    with pytest.raises(RuntimeError), recorder.measure():
        raise RuntimeError("boom")

    assert len(recorder) == 1, "a failed call has no meaningful duration"
    assert recorder.errors == 1


def test_timed_reports_elapsed_and_restores_the_collector() -> None:
    was_enabled = gc.isenabled()
    recorder = LatencyRecorder("region")
    with timed(recorder) as elapsed:
        sum(range(10_000))
    assert elapsed[0] > 0
    assert len(recorder) == 1
    assert gc.isenabled() == was_enabled


def test_histogram_counts_every_sample() -> None:
    samples = sorted([1, 5, 10, 50, 100, 500, 1000, 5000, 10_000])
    hist = histogram(samples, buckets=8)
    assert hist.total == len(samples)
    assert len(hist.counts) == 8
    assert len(hist.edges_ns) == 9
    assert hist.log_scale


def test_histogram_edges_are_monotonic() -> None:
    hist = histogram(list(range(1, 5000)), buckets=12)
    assert list(hist.edges_ns) == sorted(hist.edges_ns)


def test_linear_histogram_is_available() -> None:
    hist = histogram([1, 2, 3, 4], buckets=4, log_scale=False)
    assert not hist.log_scale
    assert hist.total == 4


def test_empty_histogram() -> None:
    hist = histogram([])
    assert hist.counts == () and hist.total == 0


def test_stats_serialise_with_their_histogram() -> None:
    recorder = LatencyRecorder("stage")
    for v in (5, 6, 7):
        recorder.record(v)
    payload = recorder.stats().to_dict()
    assert payload["name"] == "stage"
    assert payload["percentiles"]["p50"] == 6
    assert sum(payload["histogram"]["counts"]) == 3
