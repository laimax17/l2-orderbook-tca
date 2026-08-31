# l2-orderbook-tca — template branch

> **This branch is an exercise, not a finished project.** Everything around the
> algorithms is written and tested; the four modules worth writing by hand are
> signatures, docstrings and a red test suite. Clone it and fill them in.
>
> ```bash
> git clone -b template https://github.com/laimax17/l2-orderbook-tca
> cd l2-orderbook-tca && uv sync --all-extras
> uv run pytest              # 84 red, 119 green. The 84 are the work.
> ```
>
> `main` carries a completed implementation. It is a spoiler for every design
> question below, so read it afterwards rather than before — and expect your
> answers to differ from its, because most of these questions have more than one
> defensible answer.

Real-time L2 order book reconstruction and execution cost analysis (TCA) from
Kraken's public WebSocket feed.

Async ingest with reconnect and staleness detection, lossless capture to JSONL,
deterministic replay, hour-partitioned Parquet storage, a latency benchmark
harness and plotting — all complete and tested. The reconstruction, signal and
TCA algorithms themselves are specified but **not implemented**; see
[The core](#the-core-deliberately-unimplemented).

Read-only against the exchange's public feed. There is no authenticated
endpoint, no order entry, and no broker credential anywhere in the repository.

---

## Architecture

<!-- TODO: architecture diagram goes here. -->

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
uv sync                       # runtime + dev
uv sync --all-extras          # adds matplotlib for the plots
uv run pytest -m "not core"   # infrastructure suite -- green
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

# 5. Time recv -> book-updated, per stage, with histograms
uv run l2tca bench data/raw/synthetic.jsonl --histogram

# 6. Plot
uv run l2tca bench data/raw/synthetic.jsonl --json > bench.json
uv run l2tca plot latency --report bench.json --out latency.png
```

Capturing a live session:

```bash
uv run l2tca record --symbol BTC/USD --depth 100 --duration 600
```

writes `data/raw/kraken_book_BTC-USD_d100_<timestamp>.jsonl`. Captures are
gitignored; they are data, not source.

Human-readable output goes to stdout, structured events to stderr as JSON:

```bash
uv run l2tca record --duration 600 > summary.txt 2> events.jsonl
```

## Package map

| Package | Status | What it does |
|---|---|---|
| `feed/` | complete | WS client, reconnect, staleness watchdog, parsing, capture, replay |
| `io/` | complete | Arrow schemas, hour-partitioned Parquet writer, validating Polars readers |
| `bench/` | complete | recv → book-updated timing, percentiles, histograms |
| `plot/` | complete | Depth ladder, spread series, latency histogram |
| `cli/`, `logging.py`, `config.py` | complete | Commands, JSON logging, configuration |
| `book/order_book.py` | **stub** | L2 reconstruction, depth trimming |
| `book/sequence.py` | **stub** | Integrity and resynchronisation state machine |
| `signals/microstructure.py` | **stub** | Imbalance, micro-price, quoted and effective spread |
| `tca/analysis.py` | **stub** | Arrival benchmark, interval VWAP, child orders, attribution |

## The core, deliberately unimplemented

`book/`, `signals/` and `tca/` raise `NotImplementedError`. This is the shape of
the repository on purpose: the algorithms are the part worth writing by hand,
and everything around them is finished so the first line of the book can be run
against a real recording and benchmarked the same minute.

The stubs state the input format, the complexity targets, and the questions each
piece has to answer — not the answers. [`docs/CORE.md`](docs/CORE.md) collects
the open design questions.

**The tests are the answer key.** They assert hard-coded expected values, and
they are red:

```bash
uv run pytest                  # 119 pass, 84 fail -- the 84 are the work
uv run pytest -m core          # the core suite alone
uv run pytest -m "not core"    # the infrastructure alone: green. What CI runs.
```

A bare `pytest` is red on purpose, and there is deliberately no marker filter in
`addopts`: a filter there applies to every invocation, including the explicit
node ids an IDE passes when you click a single test, which silently deselects it
instead of running it. Implement a method, rerun, and the tests tell you whether
you got it right.

## Results

Nothing measured yet — the book is unimplemented, so there is no update path to
time and no reconstructed depth to plot. Filled in once the core exists:

| | |
|---|---|
| recv → book-updated, p50 / p99 / p99.9 | TBD |
| `apply_update` p50 / p99 | TBD |
| `view(10)` p50 / p99 | TBD |
| Sustained throughput (updates/s) | TBD |
| Checksum verification rate over a 10-minute capture | TBD |
| Internal representation A/B (dict vs sorted vs tick array) | TBD |
| Capture size, 10 minutes at depth 100 | TBD |

Measured with `l2tca bench <capture>`; every report carries the machine it ran
on, because latency numbers without it are not comparable.

## Design decisions

Reasoning in more detail lives in the module docstrings.

**Two clocks on every frame.** `time.perf_counter_ns()` is monotonic and immune
to NTP steps, so it is the only clock used for latency arithmetic; it has no
epoch, so it means nothing across processes. `time.time_ns()` is comparable
against exchange timestamps but can jump. Both are stamped at receipt, and the
capture header pairs them once so a recording's monotonic stamps can be anchored
to wall clock after the fact.

**Prices are `Decimal`; floats appear only at the Parquet boundary.** Kraken's
book checksum is computed over the exact digits the exchange sent, so a round
trip through binary float can make a correct implementation report corruption.
Price levels are also dictionary keys, where float drift is a correctness bug
rather than a display bug.

**Capture is lossless and replay is deterministic.** Frames are recorded as the
exact text received, never re-serialised. Replay defaults to the recorded
timestamps and no pacing, which makes every downstream result a pure function of
the file. `--speed` scales the recorded gaps when wall-clock behaviour is what is
being tested.

**Recordings show their own gaps.** Connect, disconnect and reconnect are written
into the capture as `control` records. A replay that silently glossed over a
two-second reconnect would look like a clean session and quietly invalidate any
staleness analysis run on it.

**Full jitter on reconnect.** Delays are `uniform(0, min(cap, base * 2^n))`
rather than the exponential value itself. After a venue-wide outage every client
reconnects at once; an unjittered schedule keeps the endpoint down.

**A ping before declaring a connection dead.** Kraken heartbeats at least once a
second, so silence is diagnostic — but a half-open TCP connection looks
identical to an idle one until you write to it. The first silent window buys one
application-level ping; only the second declares the connection dead.

**Hour-partitioned Parquet with pinned Arrow schemas.** Inferred schemas drift —
an all-null column is `null` type one hour and `double` the next, and the two
files stop scanning together. `schema_version` lives in the rows, not just the
path, so it survives a file being copied.

**Tail latency, not mean.** Every sample is kept, and percentiles are
nearest-rank so a reported `p99` is a latency that actually occurred. Warmup and
snapshot rebuilds are excluded from the update distribution: the first frames
pay for interpreter warmup, and a rebuild is a different operation with a
different cost.

## Synthetic data caveat

`l2tca synth` produces frames with the right *shape* — snapshot then incremental
updates, deletes, heartbeats — from a seeded RNG, so the plumbing can be
exercised without a network. The price process is a lazy random walk and the
depth profile is arbitrary. **Nothing it produces has market meaning.** Use a
real capture for anything with a conclusion attached.

Synthetic frames deliberately carry no `checksum` field: fabricating one would
require the very CRC32 implementation it is meant to validate, and a
self-consistent fake would confirm a wrong implementation against itself.

Tests that need real data look for `tests/fixtures/sample.jsonl` and skip when it
is absent. Record one and commit a trimmed slice to activate them.

## Layout

```
src/l2tca/
  feed/      WS client, reconnect, parsing, JSONL capture, replay
  book/      order book core + resync state machine   [stub]
  signals/   microstructure factors                   [stub]
  tca/       execution cost analysis                  [stub]
  io/        Arrow schemas, Parquet writer/reader
  bench/     latency harness
  plot/      figures over stored Parquet
  cli/       subcommands
tests/
  factories.py   test data factory
  fixtures/      a recorded sample goes here
  test_order_book.py test_sequence.py
  test_microstructure.py test_analysis.py   ← the core suite, red by design
data/raw/    captures (gitignored)
notebooks/
docs/CORE.md
```

Packages live under a single `l2tca/` distribution package rather than as
top-level `feed/`, `book/`, `io/`: a top-level `io` package is unimportable,
because the standard library's is already in `sys.modules` before any path entry
is consulted.

## License

MIT.
