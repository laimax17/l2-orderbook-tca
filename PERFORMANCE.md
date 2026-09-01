# Performance

One optimisation, measured before and after on the same machine, same capture,
same procedure. Nothing here is estimated or scaled from anything.

## Method

- Workload: the committed fixture, `tests/fixtures/sample.jsonl.gz` — 4,997
  frames of BTC/USD at depth 100 over 143 seconds, replayed with the first 500
  frames dropped as warmup and snapshot rebuilds excluded.
- **Three full runs per configuration**, reporting the median across runs and
  the run-to-run spread. A single run on a laptop cannot distinguish a 10%
  improvement from thermal noise, and the spread is what says whether a
  difference is real.
- Timing is per call, via `time.perf_counter_ns`, with GC frozen for the
  measured region. Throughput is wall-clock over the whole replay rather than
  the inverse of a median, so it carries the timing overhead honestly.
- Environment: CPython 3.11 on x86-64 Linux, in a container. **Absolute numbers
  are not comparable with the ones in the README**, which were measured on an
  Apple Silicon laptop. Only the before/after ratios here are meaningful, and
  they were produced back to back on one machine.

Reproduce:

```bash
uv run l2tca bench <capture> --warmup 500 --json
```

## The bottleneck was not where it looked

The README's first measurements showed the read path costing three times the
write path — `view(10)` at 13.75 µs against `apply_update` at 4.38 µs. The
obvious suspect was the internal representation: `SortedDict` is a tree, and
walking one to collect ten levels is not free.

It was not the representation. Isolating `depth_levels` from the `BookView`
construction around it put essentially the whole cost in the former, and reading
it showed why:

```python
top_bids.append(Level(bids_keys[-i], self.bids[bids_keys[-i]].qty))
```

The values stored in the book **are** `Level` objects. Every level was being
taken apart and rebuilt into an identical one, which costs an allocation — and,
the expensive half, a second lookup keyed by a `Decimal`, whose hash is far
dearer than an integer's. Twenty levels a call, forty tree operations, twenty of
them redundant.

The fix reads the values view directly, six lines:

```python
bids = self.bids.values()
asks = self.asks.values()
return tuple(reversed(bids[-n:])), tuple(asks[:n])
```

Had the data-structure comparison been run first, all three candidates would
have carried this overhead and the conclusion drawn from it would have been
about the wrong thing.

## Before / after

Median of three runs, microseconds:

| Stage | Before p50 | After p50 | Change | Before p99 | After p99 |
|---|---:|---:|---:|---:|---:|
| `recv → book-updated` | 47.796 | **31.628** | **−33.8%** (1.51×) | 139.694 | 76.750 |
| `book.view(n=10)` | 20.869 | **7.066** | **−66.1%** (2.95×) | 68.466 | 24.032 |
| `book.apply_update` | 10.078 | 9.870 | −2.1% | 37.500 | 37.782 |
| `parse` | 13.385 | 13.027 | −2.7% | 64.322 | 43.923 |
| `book.apply_snapshot` | 469.126 | 414.860 | −11.6% | — | — |
| Throughput (msg/s) | 15,390 | **22,908** | **+48.8%** | | |

Run-to-run spread on the p50s was under 3% everywhere except
`apply_snapshot`, which is a single sample per run and correspondingly noisy.
Every change claimed above is an order of magnitude larger than that spread.

`apply_update` and `parse` are unchanged within noise, which is the point: the
edit was local to the read path and the numbers say so. A speedup that moved
every stage would have meant the measurement, not the code, had changed.

## Correctness

The optimisation touches the function whose output feeds the integrity check, so
"the tests pass" is not the standard here.

- `depth_levels` was run against the previous implementation over **24,260
  (frame, n) pairs** — every one of the 4,852 update frames in the capture, at
  n = 1, 5, 10, 100 and 250. Identical output on all of them.
- Checksums after the change: **4,852 / 4,852 verified (100.00%)**, zero
  sequence gaps. This is the strongest available check, because the checksum is
  computed over exactly the top ten levels this function returns: taking both
  sides from the same end of the price-ordered structure returns the *worst* ten
  and fails on the first frame.
- Two behaviours the rewrite introduced or relies on are now pinned by tests:
  `depth_levels(0)` returns nothing rather than everything (`values()[-0:]` is
  the whole side), and a tuple already handed to a caller is not disturbed by a
  later update.

## Limitations

- This measures **market-data processing latency in one process**: the time from
  a frame being read off the socket to the book reflecting it. It is not
  tick-to-trade, not exchange-to-exchange, and there is no order entry anywhere
  in this repository.
- This is not a production HFT system, and CPython is not a low-latency runtime.
  A C++ order book does this work in hundreds of nanoseconds. The numbers here
  are useful as a before/after and as a statement about where the cost sits, not
  as a claim about absolute speed.
- Tail latency reflects the Python runtime and OS scheduling as much as the
  code. p99.9 and max should be read with that in mind.
- The benchmark is a controlled replay from a file. It has no network, no
  contention with a live socket, and a page cache that is warm after the first
  run.

## Not measured

The README lists an internal-representation A/B — `SortedDict` against a plain
dict sorted on read, against a tick-indexed array. It is unfilled, and it is not
an oversight: it means writing the book two more ways and benchmarking three,
which is a project rather than a measurement. `run_book_benchmark` already takes
a `book_factory`, so the harness for it exists.

Two things measured while sizing that work, which constrain it:

- The book spans a **median of 1,588 ticks** at 0.1 (max 1,961), and holds 200
  levels inside that span — an occupancy of **12.6%**. A dense array is 87%
  empty, so extracting the top ten means scanning outward across mostly empty
  slots rather than indexing. Whether that beats ten tree lookups is genuinely
  open, which is what makes the comparison worth running.
- Over 143 seconds the mid drifted 349 ticks, and the whole capture spans 2,091
  slots. A ten-minute capture would fit comfortably in a fixed array sized from
  its own min and max, so a replay benchmark needs no re-centring logic — the
  part of a tick array most likely to harbour a bug.
