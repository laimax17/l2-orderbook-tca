# l2-orderbook-tca

Real-time L2 order book reconstruction and execution cost analysis (TCA) from
Kraken's public WebSocket feed.

Async ingest with reconnect and staleness detection, lossless capture to JSONL,
deterministic replay, hour-partitioned Parquet storage, and a latency benchmark
harness — all complete and tested. The reconstruction, signal and TCA algorithms
themselves are specified but **not implemented**; see
[Deliberately unimplemented](#deliberately-unimplemented).

Read-only against the exchange's public feed. There is no authenticated
endpoint, no order entry, and no broker credential anywhere in the repository.

---

## Scope

Phase one, and nothing beyond it:

| | |
|---|---|
| Exchange | Kraken public WebSocket v2 |
| Channel | `book`, `depth=100` |
| Symbol | `BTC/USD` (configurable; `XBT/USD` is accepted and normalised) |
| Storage | JSONL captures, Parquet tables |

Explicitly out of scope: multiple exchanges, live order placement, a web
frontend, a database.

## Install

```bash
uv sync                     # or: uv venv && uv pip install -e '.[dev]'
uv run pytest
uv run ruff check .
```

Python 3.11+.

## Quick start

No network needed for any of this except `record`:

```bash
# 1. Generate a deterministic synthetic capture (shape only — see the caveat below)
uv run l2tca synth --updates 5000 --out data/raw/synthetic.jsonl

# 2. Summarise it: frame mix, inter-arrival gaps, sequence gaps
uv run l2tca inspect data/raw/synthetic.jsonl

# 3. Replay it — unpaced, or at 10x wall clock
uv run l2tca replay data/raw/synthetic.jsonl --speed 10

# 4. Flatten it into the partitioned tick table
uv run l2tca convert data/raw/synthetic.jsonl --out data/parquet

# 5. Time parse / apply / view per call
uv run l2tca bench data/raw/synthetic.jsonl
```

Capturing a live session:

```bash
uv run l2tca record --symbol BTC/USD --depth 100 --duration 600
```

writes `data/raw/kraken_book_BTC-USD_d100_<timestamp>.jsonl`. Captures are
gitignored; they are data, not source.

## Architecture

```
        Kraken WS v2                    ┌──────────────┐
             │                          │ data/raw/    │
             ▼                          │  *.jsonl     │
   ┌───────────────────┐    write       └──────┬───────┘
   │ feed/kraken.py    │ ─────────────────────►│
   │  reconnect        │                       │ read
   │  staleness ping   │                       ▼
   │  stamps 2 clocks  │              ┌──────────────────┐
   └────────┬──────────┘              │ feed/replay.py   │
            │                         │  deterministic   │
            │      ┌──────────────────┤  paced or not    │
            ▼      ▼                  └────────┬─────────┘
     ┌──────────────────┐                      │
     │ feed/messages.py │◄─────────────────────┘
     │  Decimal parse   │
     └────────┬─────────┘
              │
   ┌──────────┴───────────┬──────────────────┐
   ▼                      ▼                  ▼
┌────────────┐   ┌─────────────────┐   ┌────────────┐
│ book/  ▒▒▒ │──►│ signals/    ▒▒▒ │   │ io/        │
│            │   │ tca/        ▒▒▒ │   │  Parquet   │
└─────┬──────┘   └─────────────────┘   └────────────┘
      │
      ▼
┌────────────┐
│ bench/     │  wraps the calls above, reports p50…p99.9
└────────────┘

▒▒▒ = specified, not implemented
```

| Package | Status | What it does |
|---|---|---|
| `feed/` | complete | WS client, reconnect, heartbeat watchdog, parsing, capture, replay |
| `io/` | complete | Arrow schemas, hour-partitioned Parquet writer, validating Polars readers |
| `bench/` | complete | Per-call latency sampling and percentile reporting |
| `cli.py` | complete | `record`, `synth`, `inspect`, `replay`, `convert`, `bench` |
| `book/` | **stub** | L2 reconstruction, depth trimming, Kraken CRC32 checksum |
| `signals/` | **stub** | Imbalance, micro-price, book pressure, depth slope |
| `tca/` | **stub** | Shortfall, effective/realized spread, book walk, TWAP |

## Deliberately unimplemented

`book/`, `signals/` and `tca/` raise `NotImplementedError`. This is the point of
the repository's shape: the algorithms are the part worth writing by hand, and
everything around them is finished so that the first line of the book can be
run against a real recording and benchmarked the same minute.

Each stub carries its full specification in its docstring — invariants, edge
cases, the wrong implementations that look right — and
[`docs/SPEC.md`](docs/SPEC.md) collects them with the reasoning behind each
design choice.

The specifications are executable. `tests/spec/` encodes them as tests, marked
`xfail` so the suite is green today:

```bash
uv run pytest tests/spec -rX      # shows what each stub must satisfy
```

Implement a method, delete its `xfail` marker, and the test tells you whether
you got it right.

## Design decisions

Notes on the choices an interviewer is likely to ask about — the reasoning is
in the module docstrings, in more detail.

**Two clocks on every frame.** `time.perf_counter_ns()` is monotonic and immune
to NTP steps, so it is the only clock used for latency arithmetic; it has no
epoch, so it means nothing across processes. `time.time_ns()` is comparable
against exchange timestamps but can jump. Both are stamped at receipt, and the
recording header pairs them once so a capture's monotonic stamps can be anchored
to wall clock after the fact.

**Prices are `Decimal`, and floats appear only at the Parquet boundary.**
Kraken's book checksum is computed over the exact digits the exchange sent, so a
round trip through binary float can make a correct implementation report
corruption. Price levels are also dictionary keys, where float drift is a
correctness bug rather than a display bug.

**Capture is lossless and replay is deterministic.** Frames are recorded as the
exact text received, never re-serialised. Replay defaults to the recorded
timestamps and no pacing, which makes every downstream result a pure function of
the file. `--speed` scales the recorded gaps when wall-clock behaviour is what
is being tested.

**Recordings show their own gaps.** Connect, disconnect and reconnect are
written into the capture as `control` records. A replay that silently glossed
over a two-second reconnect would look like a clean session and quietly
invalidate any staleness analysis run on it.

**Full jitter on reconnect.** Delays are `uniform(0, min(cap, base * 2^n))`
rather than the exponential value itself. After a venue-wide outage every client
reconnects at once; an unjittered schedule keeps the endpoint down.

**Hour-partitioned Parquet with pinned Arrow schemas.** Inferred schemas drift —
an all-null column is `null` type one hour and `double` the next, and the two
files stop scanning together. `schema_version` lives in the rows, not just the
path, so it survives a file being copied.

**Tail latency, not mean.** Every sample is kept, and percentiles are
nearest-rank so a reported `p99` is a latency that actually occurred. A mean
hides exactly the behaviour that matters: the update that walks the whole book,
or the allocation that triggers a collection.

## Synthetic data caveat

`l2tca synth` produces frames with the right *shape* — snapshot then
incremental updates, deletes, heartbeats — from a seeded RNG, so the plumbing
can be exercised without a network. The price process is a lazy random walk and
the depth profile is arbitrary. **Nothing it produces has market meaning.** Use
a real capture for anything with a conclusion attached.

Synthetic frames deliberately carry no `checksum` field: fabricating one would
require the very CRC32 implementation it is meant to validate, and a
self-consistent fake would confirm a wrong implementation against itself.

## Layout

```
src/l2tca/
  feed/      WS client, reconnect, message parsing, JSONL capture, replay
  book/      order book core                          [stub]
  signals/   microstructure factors                   [stub]
  tca/       execution cost analysis                  [stub]
  io/        Arrow schemas, Parquet writer/reader
  bench/     latency harness
  cli.py
tests/
  spec/      executable specifications for the stubs (xfail)
data/raw/    captures (gitignored)
notebooks/
docs/SPEC.md
```

Packages live under a single `l2tca/` distribution package rather than as
top-level `feed/`, `book/`, `io/` — a top-level `io` package would shadow the
standard library's on any path that includes `src/`.

## License

MIT.
