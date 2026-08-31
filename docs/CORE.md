# The core: the decisions it rests on

`book/`, `signals/` and `tca/` are written. This file is the record of what had
to be decided before they could be, and which way each decision went — the
questions the stubs asked, and the answers the implementations now encode.

It was written before the code, in the order the code was built:
**book → sequence → signals → TCA.** Signals and TCA both consume a `BookView`,
so neither could be validated against real data until the book produced one.

```bash
uv run pytest -m core                         # the 81 tests over these modules
uv run pytest tests/test_order_book.py        # one file
uv run pytest -m "not core"                   # the 120 infrastructure tests
```

The tests carry hard-coded expected values, so a changed decision shows up as a
specific number moving rather than as a vague failure.

---

## 1. `book/order_book.py` — reconstruction

Given: frames arrive parsed, with `Decimal` prices and quantities and an
optional checksum. See the module docstring for the complexity targets.

Open:

- **Internal representation.** Reads happen as often as writes here — every
  applied frame emits a view. What does that imply, and can you show it with
  numbers rather than argue it? `l2tca bench` exists for exactly this, and
  `run_book_benchmark(book_factory=...)` takes an alternative implementation so
  two can be run against the same capture.
- **Key type.** Prices arrive as `Decimal`. Staying with them, or moving to
  scaled integers, is a trade between speed and what the checksum needs.
- **Depth window.** The feed is depth-limited. What follows for levels that
  leave the window, and what does *not* catch that mistake?
- **Absolute or delta.** A non-zero quantity on the wire is one or the other.
  How would you establish which from a capture?
- **Crossed books.** Is `best_bid >= best_ask` a market state or a bug, and what
  should the book do about it?
- **Unknown deletes.** A delete for a price the book does not hold: fault, or
  expected?

## 2. `book/sequence.py` — integrity and resync

The file's top comment lists the states this machine needs and the conditions
that must cause a transition. Mapping the second list onto the first is the
work.

Open:

- **What plays the role of a sequence number?** Kraken's v2 `book` channel does
  not carry one. Answer this before anything else in the module; every method
  depends on it.
- **Buffering.** Is it bounded, and what happens when it fills? When a snapshot
  lands, which buffered frames are still relevant?
- **The checksum.** How many levels, in what order, rendered how, hashed how?
  Where do the per-pair precisions come from, and what happens to every checksum
  if they are wrong? Kraken's API docs specify the construction exactly — read
  it there rather than inferring it from a capture.
- **Escalation.** One mismatch, or several in a row? Is serving a diverged book
  better or worse than a gap in coverage?

Note that this is the only part of the project that cannot be proven against
synthetic data: `l2tca synth` emits no checksum on purpose, because a fabricated
one would validate a wrong implementation against itself. See
[Recording the test fixture](#recording-the-test-fixture) below.

## 3. `signals/microstructure.py` — four factors

The docstrings give the mathematical definition and the economic meaning of each,
because those are properties of the quantity, not decisions about the code.

Open:

- **Undefined inputs.** An empty side, a zero total quantity, a zero mid. Each
  function has them. Decide what a caller gets, and be consistent across all
  four — noting that `0.0` is also a legitimate result for several of them.
- **Depth parameter.** `order_book_imbalance` takes a level count. What does
  raising it buy, and what does it cost?
- **Contemporaneity.** `effective_spread` needs the book "at the fill". Which
  instant is that, when fills and book updates arrive on different paths? This
  one is shared with the TCA questions below.

## 4. `tca/analysis.py` — execution cost

**These decisions are made.** They are recorded here rather than left open,
because every number this package produces inherits them and a reader is
entitled to know what they inherited. Each is defensible, none is the only
answer, and reversing one is a legitimate change -- provided the reasoning
replaces the reasoning below rather than merely contradicting it.

Notation, used throughout:

| | |
|---|---|
| `d` | `side.sign` -- `+1` for a buy, `-1` for a sell |
| `P0` | the arrival mid |
| `Mi` | the mid contemporaneous with fill `i` |
| `Pi`, `qi` | the price and quantity of fill `i` |
| `Q`, `Qt` | quantity filled, quantity targeted |
| `Pend` | the mid at the end of the execution window |

### 4.1 The arrival benchmark

**The instant: the decision** (`Order.decision_ns`).

Shortfall in Perold's original sense is measured against the price at which the
decision was taken, and that is the only choice that carries the cost of the
delay between deciding and reaching the venue. Measuring from arrival at the
venue excuses that delay; the cost is still paid, it just stops being anyone's.

The cost of this choice is that the numbers include latency the trader may not
control. That is the intended reading, not a defect.

**The price: the mid.**

Neutral. Taking the touch on the trading side (the ask, for a buy) charges half
a spread before anything has happened, so a flawless execution still shows a
cost -- conflating "the market charges a spread" with "we executed badly". With
the mid, spread cost appears explicitly as its own attribution layer instead.

The micro-price is a better fair-value estimate and is deliberately not used: it
is itself a modelling choice, and a benchmark should carry as little research as
possible.

**Contemporaneity: the last view with `recv_ns <= t`.**

The most consequential rule in the module. Taking the *next* view uses a book
that had not yet arrived -- look-ahead bias, and it flatters every number it
touches.

The same rule applies everywhere: the arrival benchmark, per-fill benchmarks,
and VWAP bucketing. A different rule in one place would need its own defence.

When no view exists at or before `t`, raise. Falling back to the first future
view is the look-ahead this rule exists to prevent.

### 4.2 Child order simulation

**Schedule: TWAP.** Equal slices at equal intervals across the window; ten
slices unless a caller says otherwise.

It is a real benchmark strategy rather than a placeholder, it has no free
parameter beyond the slice count, and the alternatives are unavailable or
unwise: VWAP and participation-rate schedules need traded volume, which the
`book` channel does not carry, and an adaptive schedule would make the
simulator's output depend on a signal -- measuring the signal, not the
execution.

**Fills: aggressive only.** Every child crosses the spread and walks the
opposite side of the book.

This is the strongest constraint in the package and it comes from the data.
Without trade prints there is no evidence of *when* a resting order would have
filled, and modelling that requires a queue position which L2 data does not
contain -- it aggregates each price level, so ten units at a price could be one
order or ten. An aggressive-only simulator is therefore an upper bound on cost
whose every assumption is visible in the book, rather than a lower one resting
on a queue model that cannot be validated.

**Depth exhausted: fill what is there, carry the remainder to the next slice.**
Never extrapolate past the last visible level; an invented price for
unfillable quantity is a fabricated number that reads as a real estimate.

**Remainder at the end of the window: left unfilled.** Its cost is opportunity
cost and belongs in the attribution, not in an invented fill.

**Granularity: one `Fill` per book level consumed.** A single averaged fill per
slice hides that the order walked five levels, which is the thing worth seeing.

### 4.3 Attribution

Four layers that sum exactly to the total. In currency:

```
spread_ccy       =  sum over fills of  qi * (Pi - Mi) * d
timing_ccy       =  sum over fills of  qi * (Mi - P0) * d
fees_ccy         =  sum over fills of  fee_i
opportunity_ccy  =  (Qt - Q) * (Pend - P0) * d
```

They sum by construction, not by approximation:

```
spread_ccy + timing_ccy = sum of qi * [(Pi - Mi) + (Mi - P0)] * d
                        = sum of qi * (Pi - P0) * d
                        = Q * (average fill price - P0) * d
```

which is the execution cost; fees and opportunity cost complete the shortfall.
There is no residual, and a decomposition with a residual is not one.

**Denominator: `Qt * P0`, the target notional, shared by all four layers.**
Dividing by *filled* notional is the common error: it makes a badly underfilled
order look cheap, which is precisely the case shortfall exists to penalise. A
shared denominator is also what lets the layers add up.

```
<layer>_bps = <layer>_ccy / (Qt * P0) * 1e4
total_bps   = the sum of the four
```

**Sign: positive means cost**, on both sides, via `d`. Every layer goes through
the same helper; a per-layer sign flip is how a TCA report ends up flattering
sells and punishing buys.

**Keys are constant.** All five keys are returned whether or not anything
filled. With no fills, the first three are zero and opportunity carries the
whole order.

**Market impact is deliberately not a layer.** Separating the price move the
order caused from the move the market would have made anyway needs either a
control -- what the price would have done without this order -- or a trade feed
to infer it from. Neither exists here. The move is reported whole, as `timing`.
A number labelled "impact" produced without either would look authoritative and
mean nothing.

### 4.4 Interval VWAP

For each `(ts, volume)` bucket, take the mid of the view contemporaneous with
`ts` under the rule in 4.1, and weight it by that bucket's volume:

```
vwap = sum(volume_i * mid_i) / sum(volume_i)
```

Buckets with no view at or before them are skipped. Raise when the window is
empty, when every bucket was skipped, or when the volumes sum to zero.

---

## What is already done

The WebSocket client with reconnect and staleness detection, message parsing,
lossless capture, deterministic replay, the Arrow schemas and hour-partitioned
Parquet writer, the validating Polars readers, the latency harness, the plots,
structured logging and the CLI. Reasoning for each is in the module docstrings
and summarised in the README's design notes.


---

## Recording the test fixture

Several tests skip until a real capture is committed:

```
SKIPPED  no recorded sample at tests/fixtures/sample.jsonl[.gz]
```

They skip rather than fail because a capture is data, not source -- it cannot be
generated, only recorded from the live exchange. The synthetic generator covers
the *shape* of the feed, but three things only a real capture can establish:

* **Checksums.** Synthetic frames carry none, on purpose. This is the only way
  to prove `verify_checksum` against something other than itself.
* **Real depth behaviour.** Levels leaving and re-entering the depth window,
  frames touching both sides at once, bursts and quiet periods.
* **Real inter-arrival timing.** What the staleness watchdog and the benchmark
  percentiles are actually tuned against.

### How to record one

```bash
# 1. Capture ten minutes. Writes data/raw/kraken_book_BTC-USD_d100_<stamp>.jsonl
uv run l2tca record --symbol BTC/USD --depth 100 --duration 600

# 2. Check it looks sane -- frame mix, gaps, sequence continuity
uv run l2tca inspect data/raw/kraken_book_BTC-USD_d100_*.jsonl

# 3. Trim to a committable slice. `head` keeps the header and the opening
#    snapshot, which is exactly what the tests need.
head -n 5000 data/raw/kraken_book_BTC-USD_d100_*.jsonl > tests/fixtures/sample.jsonl

# 4. Gzip it -- book JSONL compresses about tenfold, and both the recorder and
#    the replayer handle .gz transparently.
gzip tests/fixtures/sample.jsonl        # -> tests/fixtures/sample.jsonl.gz

# 5. Confirm the skips turned into real runs
uv run pytest -m "not core" -q          # the replay test should no longer skip
uv run pytest -m core -q                # the checksum test now has real data
```

Aim for a fixture in the low single-digit megabytes compressed. It is committed
source, so it should be small enough that nobody minds cloning it, and long
enough to contain at least one full snapshot plus a few thousand updates.

Keep the full ten-minute capture out of git -- `data/raw/` is already ignored --
and use it for benchmarking, where more data is strictly better.
