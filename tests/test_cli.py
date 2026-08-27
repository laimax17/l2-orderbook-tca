"""End-to-end exercise of every subcommand, without touching the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import SNAPSHOT_FRAME, UPDATE_FRAME, FakeWebSocket

from l2tca.cli import build_parser, main
from l2tca.io.reader import read_table


def test_parser_rejects_a_depth_kraken_does_not_serve() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["record", "--depth", "7"])


def test_synth_writes_a_capture_and_flags_it_as_synthetic(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    out = tmp_path / "s.jsonl"
    assert main(["synth", "--updates", "50", "--out", str(out)]) == 0
    assert out.exists()
    assert "no market meaning" in capsys.readouterr().err


def test_synth_is_reproducible_from_its_seed(tmp_path: Path) -> None:
    def payloads(name: str, seed: str) -> list[str]:
        out = tmp_path / name
        main(["synth", "--updates", "40", "--seed", seed, "--out", str(out)])
        return [
            json.loads(line)["payload"]
            for line in out.read_text().splitlines()
            if json.loads(line)["kind"] == "msg"
        ]

    assert payloads("a.jsonl", "3") == payloads("b.jsonl", "3")
    assert payloads("a.jsonl", "3") != payloads("c.jsonl", "4")


def test_inspect_summarises_a_capture(capture: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["inspect", str(capture)]) == 0
    out = capsys.readouterr().out
    assert "BookUpdate" in out
    assert "BookSnapshot" in out
    assert "seq gaps      : 0" in out
    assert "depth=100" in out


def test_inspect_tolerates_a_headerless_file(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    path = tmp_path / "bare.jsonl"
    path.write_text(
        json.dumps({"v": 1, "kind": "msg", "seq": 0, "recv_ns": 1,
                    "recv_wall_ns": 2, "payload": SNAPSHOT_FRAME}) + "\n"
    )
    assert main(["inspect", str(path)]) == 0
    assert "header        : (none" in capsys.readouterr().out


def test_replay_reports_throughput(capture: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["replay", str(capture), "--limit", "10"]) == 0
    assert "replayed 10 frames" in capsys.readouterr().err


def test_replay_can_echo_payloads(capture: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["replay", str(capture), "--limit", "3", "--print"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 3


def test_convert_produces_a_readable_tick_table(tmp_path: Path, capture: Path) -> None:
    root = tmp_path / "parquet"
    assert main(["convert", str(capture), "--out", str(root)]) == 0

    frame = read_table(root, "tick")
    assert frame.height > 0
    assert set(frame.get_column("side").unique()) == {"bid", "ask"}
    assert (root / "tick" / "symbol=BTC-USD").exists()


def test_convert_on_a_capture_with_no_book_frames_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    path = tmp_path / "quiet.jsonl"
    path.write_text(
        json.dumps({"v": 1, "kind": "msg", "seq": 0, "recv_ns": 1, "recv_wall_ns": 2,
                    "payload": '{"channel":"heartbeat"}'}) + "\n"
    )
    assert main(["convert", str(path), "--out", str(tmp_path / "pq")]) == 1
    assert "no book frames" in capsys.readouterr().err


def test_bench_prints_a_table(capture: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["bench", str(capture), "--warmup", "0"]) == 0
    captured = capsys.readouterr()
    assert "latency per call, microseconds" in captured.out
    assert "not implemented" in captured.out  # the book stubs
    assert "book stages are unimplemented" in captured.err


def test_bench_json_output_is_machine_readable(
    capture: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["bench", str(capture), "--warmup", "0", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stages"][0]["name"] == "parse"
    assert payload["environment"]["python"]


def test_record_captures_a_session_and_shuts_down_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Drives the real client, recorder and shutdown path against a fake socket."""
    import l2tca.feed.kraken as kraken_module

    socket = FakeWebSocket([SNAPSHOT_FRAME, UPDATE_FRAME], on_exhaust="hang")

    async def fake_connect(_config):
        return socket

    monkeypatch.setattr(kraken_module, "_default_connect", fake_connect)

    out = tmp_path / "live.jsonl"
    assert main(["record", "--duration", "0.3", "--out", str(out)]) == 0

    kinds = [json.loads(line)["kind"] for line in out.read_text().splitlines()]
    assert kinds[0] == "header"
    assert kinds.count("msg") == 3  # ack, snapshot, update
    assert "control" in kinds  # connected/subscribed are recorded
    assert socket.closed, "the socket must be closed on shutdown"
    assert "wrote" in capsys.readouterr().err
