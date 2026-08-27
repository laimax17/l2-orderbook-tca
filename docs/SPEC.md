# Specification: the parts left to implement

`book/`, `signals/` and `tca/` raise `NotImplementedError`. This document is
their specification, alongside the docstrings in the modules themselves and the
executable form in `tests/spec/`.

Order of work: **book → signals → TCA**. Signals and TCA both consume
`BookView`, so neither can be validated against real data until the book
produces one.

```bash
uv run pytest tests/spec -rX          # what each stub must satisfy
uv run pytest tests/spec/test_book_spec.py -rX
```

Each spec test is marked `xfail(raises=NotImplementedError)`. That tolerates
exactly one thing — the stub not being written yet:

| state | result |
|---|---|
| still raising `NotImplementedError` | XFAIL, suite green |
| implemented and correct | XPASS, suite green |
| implemented and **wrong** | **FAILED**, suite red |

So a half-finished implementation cannot hide behind the marker. Delete the
marker as each function is finished and the test becomes an ordinary regression
test.

---

## 1. `book/l2_book.py` — L2 reconstruction

### Invariants after every applied frame

1. No level has `qty <= 0`; a zero-quantity update is a deletion.
2. `bids` strictly descending, `asks` strictly ascending, no duplicate prices.
3. `len(bids) <= depth` and `len(asks) <= depth`.
4. Not crossed: `best_bid < best_ask` whenever both exist.
5. `seq` increments by exactly one per applied frame.

### The four ways this goes wrong

**Treating an update as a delta.** Kraken sends the *absolute* resting quantity
at a price, never a change to it. Adding produces a book that drifts slowly
rather than failing immediately, which is why the checksum matters.

**Never trimming to depth.** The feed is depth-limited. A level falling out of
the bottom of the window is not announced as a deletion, so without an explicit
trim after each update the book accumulates stale levels below the window
forever. The checksum covers only the top 10 and will not catch this.

**Treating an unknown delete as an error.** A delete for a price not in the book
is normal — it refers to a level below the window. Raising on it will kill the
feed within minutes.

**Merging a snapshot.** A snapshot is unconditional. It arrives on subscribe and
after every reconnect, and everything held before it is stale by definition.
Discard, then load.

### Internal representation

The measurement exists to answer this; do not decide it from taste.

| Representation | Apply | Read top N | Notes |
|---|---|---|---|
| `dict[Decimal, Decimal]` + sort on read | O(1) | O(n log n) | Simplest. The sort is on the hot path once per update. |
| `SortedDict` | O(log n) | O(N) | Balanced; adds a dependency. |
| Array indexed by price tick | O(1) | O(N) with cached top | Fastest; needs a fixed price band and a rebase when the market leaves it. |

Reads happen as often as writes here — every update emits a view — so the
apparently attractive first row is the one that most often loses. Benchmark it:

```bash
uv run l2tca bench data/raw/<capture>.jsonl
```

and compare p99, not the mean. Tail latency is what determines whether a
strategy acts on a stale book.

### `kraken_book_checksum`

The exchange's own integrity check, and the single most valuable test in the
project: it turns a silently wrong book into a loud failure within seconds.

1. Top 10 asks (ascending), then top 10 bids (descending). Fewer than 10 on a
   side means use what is there.
2. Render `price` with exactly `price_precision` decimals and `qty` with exactly
   `qty_precision` decimals, plain fixed-point — no exponent.
3. Remove the decimal point, then strip leading zeros from what remains.
4. Concatenate: each ask's price then quantity, then each bid's.
5. `zlib.crc32` of that ASCII string, unsigned.

Two traps:

- `Decimal.__str__` switches to exponent notation for small values, so
  `0.0000001` must be formatted or quantised, not `str`-ed.
- Step 3 strips leading zeros from the *concatenated digits*: `0.5` at 5
  decimals is `50000`, not `050000`.

The precisions are per-pair and come from Kraken's `instrument` channel or the
`AssetPairs` REST endpoint (`pair_decimals` / `lot_decimals`). They are not
derivable from book frames. Wrong precisions make every checksum fail — a useful
early signal that they are plumbed through rather than guessed.

On mismatch: increment `checksum_failures` and let the caller decide whether to
resubscribe. A mismatch means the local book has silently diverged from the
exchange's, so continuing to read from it is worse than a gap.

> The synthetic generator deliberately emits no `checksum` field. Fabricating
> one would require this same CRC32, and a self-consistent fake would validate a
> wrong implementation against itself. Test this against a real capture or a
> hand-derived vector.

---

## 2. `signals/microstructure.py` — factors

All take a `BookView` (an immutable copy) and are pure functions of it. Keep
them that way: a factor that reads a clock or carries state cannot be validated
against a recording.

Inputs are `Decimal`, outputs are `float`. Convert once, at the end, so
intermediate cancellation happens in exact arithmetic.

Return `float('nan')` — never `0.0` — for "undefined here". Zero is a real,
balanced book and reads downstream as a signal.

| Function | Definition |
|---|---|
| `order_book_imbalance(view, levels)` | `(B - A) / (B + A)` over the top `levels`, in `[-1, +1]` |
| `micro_price(view)` | `(P_b·Q_a + P_a·Q_b) / (Q_a + Q_b)` |
| `weighted_mid(view, levels)` | mean of the per-side quantity-weighted average prices |
| `relative_spread_bps(view)` | `1e4 · (P_a − P_b) / mid` |
| `book_pressure(view, levels)` | imbalance with each level weighted by `1 / (1 + \|price − mid\| / mid)` |
| `depth_slope(view, side, levels)` | OLS slope of cumulative quantity on distance from mid, through the origin |
| `log_depth_ratio(view, levels)` | `log(B / A)` |

**The micro-price weighting is crossed on purpose.** The *bid* price is weighted
by the *ask* quantity. A large resting ask means the book is heavy on the offer,
so the next trade is likelier to be a sale into the bid, pulling fair value
toward the bid. Weighting each price by its own quantity is the intuitive
version and it moves the estimate the wrong way.

**Why `book_pressure` discounts distance.** Quantity resting far from the touch
is unlikely to trade and cheap to post, so raw imbalance is easy to spoof.

**Why `log_depth_ratio` exists alongside imbalance.** The bounded ratio's
variance collapses near ±1, which makes it badly behaved as a regression input.
The log ratio is unbounded, symmetric and additive under quantity ratios.

---

## 3. `tca/execution.py` — execution cost

### Sign convention, applied without exception

Every `*_bps` result is signed so that **positive means cost**:

- Buy: `(execution_price − benchmark) / benchmark · 1e4`
- Sell: `(benchmark − execution_price) / benchmark · 1e4`

Equivalently, multiply the buy-side formula by `side.sign`. Route every function
through the same helper. A per-function sign flip is the classic way a TCA
report ends up flattering sells and punishing buys.

### Benchmarks, and the question each answers

| Benchmark | Question |
|---|---|
| Mid at arrival | Did we beat the price at the decision instant? Includes the market moving while we worked. |
| Mid at fill time | Did each fill cross a wide or a narrow spread? Measures the desk, not the market. |
| Mid at fill + horizon | How much of the cost was information, and how much reverted? |
| Interval TWAP/VWAP | Did we beat a passive schedule over the same window? |

### Specific requirements

**`implementation_shortfall_bps`** — three components against the arrival mid:
execution cost, fees, and opportunity cost on the unfilled remainder (requires
`final_mid`; when absent report the first two and record the omission in
`components` rather than returning a smaller number that reads as better
execution).

The denominator is `target_qty × arrival_mid`. Dividing by *filled* notional is
the common error and it makes a badly underfilled order look cheap — precisely
the case shortfall exists to penalise.

**`effective_spread_bps`** — `2 · side.sign · (fill.price − mid) / mid · 1e4`.
The factor of two makes it comparable to the full quoted spread: an order that
pays exactly the touch has an effective spread equal to the quoted spread.
`view_at_fill` must be the book *immediately before* the fill; the post-fill
view compares the fill against a book it already consumed.

**`realized_spread_bps`** — the same measure against the mid a horizon after the
fill. Decomposes: `effective = realized + 2 · impact`. Realized is what the
liquidity provider keeps once the price has settled; impact is what was
information. The horizon is a real modelling choice — five minutes is the equity
convention, but crypto books mean-revert faster. Sweep it.

**`walk_the_book_cost`** — consume the *opposite* side best-first (a buy lifts
asks). On a book too thin to fill, return the average over the filled portion
and `fully_filled=False`. Never extrapolate the last level: an invented price
for the unfillable remainder is a fabricated number that will be read as a real
estimate. Accuracy is bounded by feed depth — at `depth=100` this is a few
hundred thousand dollars on a liquid pair, and anything larger is extrapolation.

**`twap_benchmark`** — weight each view's mid by how long it stood, clipped to
the window. Book updates arrive in bursts, so an unweighted mean over views
overweights busy microseconds and turns a time-weighted benchmark into an
event-weighted one.

**`participation_weighted_price`** — specified but out of phase-one scope: the
`book` channel carries no trade prints, so market volume has to come from the
`trade` channel. The interface is fixed now so adding a trade feed later is
additive.

---

## What is already done

Nothing else is stubbed. Complete and tested: the WebSocket client with
reconnect and staleness detection, message parsing, lossless capture,
deterministic replay, the Arrow schemas and hour-partitioned Parquet writer, the
validating Polars readers, the latency harness, and the CLI. The reasoning
behind each is in the module docstrings and summarised in the README's design
notes.
