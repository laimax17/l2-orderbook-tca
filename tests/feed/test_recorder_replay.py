"""Capture and replay: the pair that makes offline development deterministic."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from tests.conftest import SNAPSHOT_FRAME, UPDATE_FRAME
from tests.factories import raw_messages, write_capture

from l2tca.config import FeedConfig
from l2tca.feed.recorder import JsonlRecorder, default_capture_path
from l2tca.feed.records import RECORDING_FORMAT_VERSION
from l2tca.feed.replay import ReplaySource, iter_raw_messages, iter_records, read_header
from l2tca.feed.source import ControlEvent


def test_round_trip_is_byte_exact(tmp_path: Path, config: FeedConfig) -> None:
    """A capture must reproduce the session's bytes, not a re-serialisation of them."""
    payloads = [SNAPSHOT_FRAME, UPDATE_FRAME, '{"channel":"heartbeat"}', "not json at all"]
    path = write_capture(tmp_path / "c.jsonl", raw_messages(payloads), config)

    replayed = list(iter_raw_messages(path))
    assert [m.payload for m in replayed] == payloads
    assert [m.seq for m in replayed] == [0, 1, 2, 3]
    assert [m.recv_ns for m in replayed] == [m.recv_ns for m in raw_messages(payloads)]


def test_header_anchors_the_monotonic_clock(tmp_path: Path, config: FeedConfig) -> None:
    path = write_capture(tmp_path / "c.jsonl", raw_messages([SNAPSHOT_FRAME]), config)

    header = read_header(path)
    assert header is not None
    assert header.v == RECORDING_FORMAT_VERSION
    assert header.symbol == "BTC/USD"
    assert header.depth == 100
    # perf_counter_ns has no epoch; without this pairing the file's timestamps
    # cannot be placed on a wall clock after the process exits.
    assert header.perf_ns_to_wall_ns(header.perf_epoch_ns) == header.perf_epoch_wall_ns
    shifted = header.perf_ns_to_wall_ns(header.perf_epoch_ns + 5_000_000)
    assert shifted - header.perf_epoch_wall_ns == 5_000_000


def test_control_records_make_gaps_visible(tmp_path: Path, config: FeedConfig) -> None:
    path = tmp_path / "c.jsonl"
    with JsonlRecorder(path, config) as recorder:
        recorder.write_message(raw_messages([SNAPSHOT_FRAME])[0])
        recorder.write_control(ControlEvent("disconnected", 10, 20, attempt=1, detail="boom"))
        recorder.write_control(ControlEvent("reconnect", 30, 40, attempt=1))

    controls = [r.control for r in iter_records(path) if r.control is not None]
    assert [c.event for c in controls] == ["disconnected", "reconnect"]
    assert controls[0].detail == "boom"
    # ...and they are not mistaken for market data.
    assert len(list(iter_raw_messages(path))) == 1


def test_gzip_capture_round_trips(tmp_path: Path, config: FeedConfig) -> None:
    path = write_capture(
        tmp_path / "c.jsonl.gz", raw_messages([SNAPSHOT_FRAME, UPDATE_FRAME]), config
    )
    with gzip.open(path, "rt") as fh:
        assert json.loads(fh.readline())["kind"] == "header"
    assert [m.payload for m in iter_raw_messages(path)] == [SNAPSHOT_FRAME, UPDATE_FRAME]


def test_a_truncated_capture_still_reads(tmp_path: Path, config: FeedConfig) -> None:
    """A hard kill leaves a partial last line; losing one frame beats losing the file."""
    path = write_capture(
        tmp_path / "c.jsonl", raw_messages([SNAPSHOT_FRAME, UPDATE_FRAME, UPDATE_FRAME]), config
    )
    text = path.read_text()
    path.write_text(text[: text.rindex("\n") - 30])

    assert len(list(iter_raw_messages(path))) == 2
    with pytest.raises(ValueError):
        list(iter_records(path, strict=True))


def test_a_newer_format_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.jsonl"
    path.write_text(json.dumps({"v": 99, "kind": "header", "symbol": "BTC/USD"}) + "\n")
    with pytest.raises(ValueError, match="newer than this build"):
        list(iter_records(path, strict=True))


def test_default_capture_path_encodes_the_capture_parameters(tmp_path: Path) -> None:
    path = default_capture_path(tmp_path, FeedConfig(symbol="XBT/USD", depth=25))
    assert path.name.startswith("kraken_book_BTC-USD_d25_")
    assert path.suffix == ".jsonl"
    assert default_capture_path(tmp_path, FeedConfig(), compress=True).name.endswith(".jsonl.gz")


async def test_replay_is_deterministic(capture: Path) -> None:
    async def run() -> list[tuple[int, int, str]]:
        source = ReplaySource(capture, speed=0)
        return [(m.seq, m.recv_ns, m.payload) async for m in source.stream()]

    assert await run() == await run()


async def test_replay_preserves_recorded_timestamps_by_default(capture: Path) -> None:
    replayed = [m async for m in ReplaySource(capture, speed=0).stream()]
    on_disk = list(iter_raw_messages(capture))
    assert [m.recv_ns for m in replayed] == [m.recv_ns for m in on_disk]


async def test_restamp_replaces_the_clock_only_when_asked(capture: Path) -> None:
    replayed = [m async for m in ReplaySource(capture, speed=0, restamp=True, limit=5).stream()]
    on_disk = list(iter_raw_messages(capture, limit=5))
    assert [m.payload for m in replayed] == [m.payload for m in on_disk]
    assert all(a.recv_ns != b.recv_ns for a, b in zip(replayed, on_disk, strict=True))


async def test_pacing_scales_the_recorded_gaps(capture: Path) -> None:
    """speed=N must sleep for 1/N of the recorded inter-arrival time."""
    at_1x: list[float] = []
    at_10x: list[float] = []

    async def sleep_1x(delay: float) -> None:
        at_1x.append(delay)

    async def sleep_10x(delay: float) -> None:
        at_10x.append(delay)

    async for _ in ReplaySource(capture, speed=1.0, limit=30, sleep=sleep_1x).stream():
        pass
    async for _ in ReplaySource(capture, speed=10.0, limit=30, sleep=sleep_10x).stream():
        pass

    assert len(at_10x) == len(at_1x) > 0
    assert sum(at_10x) == pytest.approx(sum(at_1x) / 10, rel=1e-9)


async def test_unpaced_replay_never_sleeps(capture: Path) -> None:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:  # pragma: no cover - must not run
        slept.append(delay)

    source = ReplaySource(capture, speed=0, sleep=fake_sleep)
    assert not source.paced
    async for _ in source.stream():
        pass
    assert slept == []


async def test_limit_stops_early(capture: Path) -> None:
    assert len([m async for m in ReplaySource(capture, speed=0, limit=7).stream()]) == 7


async def test_aclose_ends_the_replay(capture: Path) -> None:
    source = ReplaySource(capture, speed=0)
    seen = 0
    async for _ in source.stream():
        seen += 1
        if seen == 3:
            await source.aclose()
    assert seen == 3


def test_negative_speed_is_rejected(capture: Path) -> None:
    with pytest.raises(ValueError, match="speed"):
        ReplaySource(capture, speed=-1.0)


def test_sample_capture_replays_if_one_is_committed(sample_capture: Path) -> None:
    """Skips until a real recording lands in tests/fixtures/."""
    messages = list(iter_raw_messages(sample_capture))
    assert messages, "committed sample capture has no frames"
    assert [m.seq for m in messages] == sorted(m.seq for m in messages)
