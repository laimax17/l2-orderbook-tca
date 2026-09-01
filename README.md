# l2-orderbook-tca

Real-time L2 order book reconstruction and execution cost analysis (TCA) from
Kraken's public WebSocket feed.

Async ingest with reconnect and staleness detection, lossless capture to JSONL,
deterministic replay, hour-partitioned Parquet storage, a latency benchmark
harness and plotting. On top of that, the part worth writing by hand: L2
reconstruction, the checksum and resynchronisation state machine, microstructure
signals, and four execution-cost measures. See [The core](#the-core).

A frame goes from socket to updated book in **13.8 µs** at the median, 20.7 µs
at the 99th, sustaining **53k updates/s** on one core. Over six hours and 2.02
million frames, through three connection drops, the reconstruction agreed with
the exchange's own checksum on **every single frame**. All measured, none
estimated; see [Results](#results).

Read-only against the exchange's public feed. There is no authenticated
endpoint, no order entry, and no broker credential anywhere in the repository.

**Want to write the algorithms yourself?** The
[`template`](https://github.com/laimax17/l2-orderbook-tca/tree/template) branch
is this repository with the four core modules removed — infrastructure complete
and green, the algorithms left as signatures, docstrings and 84 red tests, and
the design questions reopened so the decisions are yours rather than inherited.

```bash
git clone -b template https://github.com/laimax17/l2-orderbook-tca
```

---

## Architecture

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 460, "nodeSpacing": 45, "rankSpacing": 45, "curve": "basis"}}}%%
flowchart TB
    KRAKEN(["<b>Kraken WebSocket v2</b><br/>book · depth=100 · CRC32 on every frame<br/>trade · executed prints with the aggressor's side"])

    CLIENT["<b>feed/client.py</b><br/>subscribe · heartbeat · full-jitter reconnect · staleness watchdog<br/>stamps every frame: recv_ns = perf_counter_ns(), recv_wall_ns = time_ns()"]

    CAP["<b>feed/recorder.py → data/raw/*.jsonl.gz → feed/replay.py</b><br/>lossless capture, payloads never re-serialised<br/>replayed at the original inter-arrival gaps"]

    SRC(["<b>feed/source.py — MessageSource</b><br/>live and replay both satisfy it, so nothing below this line can tell them apart"])

    PARSE["<b>feed/parser.py</b> — parse_float=Decimal, so the wire digits reach the checksum unrounded"]

    TRADES(["<b>Trades</b> — executed prints, aggressor side"])

    SEQ["<b>book/sequence.py</b><br/>disconnected → resyncing → live · buffers updates while a snapshot is in flight"]

    OB["<b>book/order_book.py</b><br/>two SortedDict sides · apply_update is transactional, undo log on reject"]

    VIEW(["<b>BookView</b> — immutable, best-first"])

    SIG["<b>signals/</b><br/>imbalance<br/>micro-price<br/>quoted and effective spread"]
    TCA["<b>tca/</b><br/>arrival benchmark<br/>interval VWAP<br/>TWAP children<br/>four-layer attribution"]
    STORE["<b>io/</b><br/>pinned Arrow schemas<br/>hive symbol= / date= / hour="]
    BENCH["<b>bench/</b> — wraps parse → apply_update → view(10)<br/>recv → book-updated, p50/p90/p99/p99.9 + histogram<br/>warmup and snapshot rebuilds excluded"]

    PQ[("<b>data/parquet/</b> — tick · snapshot · signal · trade")]
    PLOT["<b>plot/</b> — depth ladder · spread series · latency histogram"]

    KRAKEN --> CLIENT
    CLIENT -->|live| SRC
    CLIENT --> CAP -->|offline| SRC
    SRC --> PARSE --> SEQ -->|apply| OB
    PARSE -->|trade frames| TRADES
    TRADES --> TCA
    TRADES --> STORE
    OB -->|"CRC32 agrees"| VIEW
    OB -->|"drift — resubscribe for a fresh snapshot"| CLIENT
    VIEW --> SIG
    VIEW --> TCA
    VIEW --> STORE
    VIEW -.-> BENCH
    SIG --> STORE
    STORE --> PQ --> PLOT
    BENCH --> PLOT
```

Two things in that picture are the whole design, and both are easy to miss.

**Live and replay converge above the book.** `client.py` feeds the recorder and
the parser from the same stamped frames, and `replay.py` re-enters through the
same `MessageSource`. Nothing below that line — not the book, not the signals,
not the benchmark — can tell which one it is running against, so a bug seen once
on the wire can be reproduced from a file exactly, forever. Replay at `speed=0`
with the recorded timestamps kept makes a run a pure function of the capture.

**The exchange checks the reconstruction, not the author.** Kraken sends no
per-frame sequence number; what it sends is a CRC32 over the top ten levels of
each side, recomputed on every frame. The book recomputes it and compares. A
mismatch means the two books have diverged, and the only honest response is to
throw the local one away and resubscribe — which is the edge looping back to
`client.py`. This is why the results table can claim 2,021,204 of 2,021,204
frames verified rather than "the tests pass" — and it is not hypothetical: that
loop ran three times during the six-hour session below.

## Scope

Phase one, and nothing beyond it:

| | |
|---|---|
| Exchange | Kraken public WebSocket v2 |
| Channels | `book` (`depth=100`), and `trade` with `--trades` |
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

# 5. Rebuild the book and derive the snapshot + signal tables
uv run l2tca signals data/raw/synthetic.jsonl --out data/parquet

# 6. Time recv -> book-updated, per stage, with histograms
uv run l2tca bench data/raw/synthetic.jsonl --histogram

# 7. Plot
uv run l2tca bench data/raw/synthetic.jsonl --json > bench.json
uv run l2tca plot latency --report bench.json --out latency.png
```

On a capture recorded with `--trades`, the observed prints can then be priced
against the book that stood at each one:

```bash
uv run l2tca costs --root data/parquet --horizons 1 5 30
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
| `book/order_book.py` | complete | L2 reconstruction, transactional updates, depth trimming |
| `book/sequence.py` | complete | CRC32 integrity and resynchronisation state machine |
| `signals/microstructure.py` | complete | Imbalance, micro-price, quoted and effective spread |
| `tca/analysis.py` | complete | Arrival benchmark, interval VWAP, child orders, attribution |

## The core

`book/`, `signals/` and `tca/` are the algorithms — everything else in the
repository exists so that they can be run against a real recording and
benchmarked the same minute they are written.

The repository was built stubs-first for exactly that reason: the surrounding
infrastructure was finished, and the four core modules shipped as signatures,
complexity targets and a red test suite before a line of their bodies existed.
[`docs/CORE.md`](docs/CORE.md) records the design decisions each one rests on
and why they went the way they did.

```bash
uv run pytest                  # everything
uv run pytest -m core          # the 81 tests over the hand-written modules
uv run pytest -m "not core"    # the 120 infrastructure tests
```

The `core` marker survives because it is still the useful cut while working on
one of those modules. There is deliberately no marker filter in `addopts`: a
filter there applies to every invocation, including the explicit node ids an IDE
passes when you click a single test, which silently deselects it instead of
running it.

What the core does:

| | |
|---|---|
| `OrderBook` | Two `SortedDict` sides. `apply_update` is transactional — an undo log rolls the frame back whole if it would leave the book crossed, so a rejected frame never leaves a half-applied state. |
| `SequenceTracker` | Kraken sends no per-frame sequence number, so integrity rests on the CRC32 the exchange computes over the top ten levels of each side. The tracker recomputes it, and drives the disconnected → resyncing → live transitions, buffering updates while a replacement snapshot is in flight. |
| `microstructure` | Order book imbalance, micro-price (with the crossed weighting — size on the far side pulls the price toward the near one), quoted spread, effective spread. |
| `analysis` | Arrival benchmark at the decision instant, interval VWAP, a TWAP child-order simulator that walks the opposite side, and a four-layer shortfall attribution that sums to the total by construction. |
| `research` | Whether the signals predict the next move (forward returns, quantile buckets, rank IC); what real trades actually paid, split into the part the resting side kept and the part the market took back; and an execution backtest that works a TWAP across many windows of a capture and prices it against arrival and interval VWAP. |

## Results

One BTC/USD session at depth 100: **6.0 hours, 2,053,953 frames** (2,021,204
book updates, 11,158 trade frames, 95.1 frames/s), on **Apple Silicon, macOS
26.4.1, CPython 3.14.7**. Latency numbers are meaningless without the machine,
which is why every report carries its own environment block. Replayed with the
first 500 frames dropped as warmup and snapshot rebuilds excluded.

### Reconstruction

| | |
|---|---|
| Checksum agreement | **2,021,204 / 2,021,204 (100.00%)** |
| Sequence gaps | **0** |
| Disconnects survived | **3**, each followed by a resubscribe and a fresh snapshot |

Every update frame was applied to the book and checked against the CRC32 Kraken
computes over the top ten levels of each side. Six hours of continuous
incremental reconstruction with no drift, through three connection drops —
attested by the exchange's own ledger rather than by expectations written by the
same person who wrote the book. The resynchronisation path is not a unit test
here; it ran three times against a live venue and the checksums stayed exact
either side of it.

### Latency

| Stage | p50 | p99 | p99.9 |
|---|---:|---:|---:|
| `recv → book-updated` | **13.79 µs** | 20.67 µs | 59.29 µs |
| `parse` | 5.67 | 9.00 | 25.79 |
| `book.apply_update` | 4.25 | 7.29 | 19.79 |
| `book.view(n=10)` | 3.25 | 3.79 | 12.50 |
| `book.apply_snapshot` | 245.83 | — | — (4 samples) |

**53,096 updates/s** sustained: 2,021,204 of them in 38.07 s of wall clock.

`parse` is now the largest term in the path, at 1.3× an update. That is the
price of `parse_float=Decimal`, paid so the wire digits reach the checksum
unrounded — floats would be faster and would silently break the integrity check
above, so the trade is deliberate.

Reading the top of the book used to cost **3.1×** maintaining it, which is not
where the cost was expected to be. The cause was not the data structure:
`depth_levels` was rebuilding `Level` objects the book already held, paying an
allocation and a second `Decimal`-keyed lookup per level. Six lines fixed it.
[`PERFORMANCE.md`](PERFORMANCE.md) has the clean same-capture, three-run A/B —
`view(10)` 2.95× faster, end-to-end 1.51×, throughput +48.8% — the equivalence
proof over 24,260 (frame, n) pairs, and why running the representation
comparison first would have compared three candidates all carrying the same
avoidable overhead.

### Execution cost

**22,594 observed trades**, 429.3 BTC, $33.9M notional, priced against the book
that stood at each one.

| | |
|---|---|
| Quoted spread, size-weighted | **0.0918 bps** |
| Effective spread, **median** | **0.0127 bps** — one tick, exactly the quoted spread |
| Effective spread, size-weighted mean | **1.1185 bps** — 12.2× quoted |
| Notional paying exactly the quoted spread | **57.0%** (76.9% of trades) |
| Notional paying more | 43.0% (23.1% of trades) |
| Notional paying less | **0.0%** |

Read the median beside the mean; neither alone is honest. The typical trade takes
the touch and pays one tick. The mean is twelve times that because the
distribution has a thin, expensive tail: the quoted spread is 1 tick at the
median and **59 ticks at the 99th percentile**, and trades cluster where it is
wide. The gap between 76.9% of *trades* and 57.0% of *notional* at the touch is
the same fact from the other side — the larger the order, the more likely it
goes through.

Nothing traded inside the touch, which is what a venue with no hidden liquidity
and no price improvement mechanism should look like.

**Where the spread went**, size-weighted, in basis points:

| Horizon | Effective | = Realized | + Impact |
|---|---:|---:|---:|
| 1 s | 1.1185 | **−0.1284** | +1.2469 |
| 5 s | 1.1185 | **−0.5850** | +1.7036 |
| 30 s | 1.1185 | **−2.0745** | +3.1932 |

The decomposition is exact by construction. Realized spread is what the resting
side still had after the market finished reacting, and over this session it is
**negative at every horizon and falls as the horizon lengthens**: providing
liquidity at the touch gave back the whole spread and more. The entire effective
spread, and then some, is adverse selection — the takers were, on aggregate,
informed.

That is one session on one venue, size-weighted and therefore dominated by the
same tail as the mean above. It is a measurement, not a conclusion about market
making.

### Reproduce

```bash
uv run l2tca bench   <capture> --warmup 500 --histogram
uv run l2tca inspect <capture> --verify
uv run l2tca convert <capture> --out data/parquet
uv run l2tca signals <capture> --out data/parquet
uv run l2tca costs   --root data/parquet --horizons 1 5 30
uv run l2tca simulate <capture> --qty 5 --duration 60 --windows 20
```

Without a capture of your own, `tests/fixtures/sample.jsonl.gz` runs the first
three on the opening 143 s of the same session (4,852 / 4,852 checksums).

### What is not backtested

There is no strategy backtest here, and that is a decision rather than a gap.
`l2tca simulate` is an *execution* backtest: it works a TWAP across windows of a
capture and prices the result against arrival and interval VWAP, claiming
nothing about whether trading was a good idea. That is defensible on hours of
data precisely because no alpha is being estimated.

A profit-and-loss backtest would not be. Two of the reasons are structural
rather than a matter of effort. Hours of one symbol cannot support a P&L
estimate — the information coefficients measured here change sign between
horizons. And L2 data carries no queue position, so a resting order's fill
cannot be simulated at all, which leaves only aggressive execution — paying a
spread that this capture shows to be one tick at the median with a negative
realized component. That is a structural cost, not a strategy.

### Not measured

The internal-representation A/B — `SortedDict` against a plain dict sorted on
read, against a tick-indexed array — is unfilled, and it is not an oversight: it
means writing the book two more ways and benchmarking three, which is a project
rather than a measurement. `run_book_benchmark` already takes a `book_factory`,
so the harness for it exists.

Two things measured while sizing that work, which constrain it:

- The book spans a **median of 1,588 ticks** at 0.1 (max 1,961) and holds 200
  levels inside that span — an occupancy of **12.6%**. A dense array is 87%
  empty, so extracting the top ten means scanning outward across mostly empty
  slots rather than indexing. Whether that beats ten tree lookups is genuinely
  open, which is what makes the comparison worth running.
- Over 143 seconds the mid drifted 349 ticks, and that capture spans 2,091
  slots. A ten-minute capture fits comfortably in a fixed array sized from its
  own min and max, so a replay benchmark needs no re-centring logic — the part
  of a tick array most likely to harbour a bug.

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
  book/      order book core + resync state machine   [core]
  signals/   microstructure factors                   [core]
  tca/       execution cost analysis                  [core]
  io/        Arrow schemas, Parquet writer/reader
  bench/     latency harness
  plot/      figures over stored Parquet
  cli/       subcommands
tests/
  factories.py   test data factory
  fixtures/      a recorded sample goes here
  test_order_book.py test_sequence.py
  test_microstructure.py test_analysis.py   ← the core suite (`-m core`)
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
