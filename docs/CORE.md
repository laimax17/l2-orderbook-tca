# The core: what is left to write, and what has to be decided first

`book/`, `signals/` and `tca/` raise `NotImplementedError`. This file collects
the open questions. It does not answer them — that is the point of the
repository's shape.

Order of work: **book → sequence → signals → TCA.** Signals and TCA both consume
a `BookView`, so neither can be validated against real data until the book
produces one.

```bash
uv run pytest -m core                         # the whole development target
uv run pytest -m core tests/test_order_book.py
```

The tests **do** contain answers — hard-coded expected values, as a red bar to
code against. The docstrings ask; the tests pin. If you want to derive a
decision yourself, read the stub first and the test afterwards.

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
one would validate a wrong implementation against itself. Record a real capture,
trim it, and commit it to `tests/fixtures/sample.jsonl`.

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

Three decisions, spelled out in the module's top comment and reproduced here
because they are the substance of the whole package:

**Arrival price.** Which instant — the decision, arrival at the venue, the first
fill? Which price at that instant — mid, the touch on the trading side,
micro-price, a short average? The book updates continuously and a fill lands
between two updates: which view counts as contemporaneous, and does the rule
differ between the arrival benchmark and per-fill benchmarks?

**Child orders.** How is the parent split — fixed slices, fixed intervals,
participation rate, adaptive? When does a child fill; does a resting child ever
fill, given that the `book` channel carries no trade prints? What happens to a
child the book cannot fill? What does an unfilled remainder cost?

**Attribution.** How many layers, and which? Do they sum exactly to the total,
and what does a residual mean? What is the denominator, and is it the same for
every layer? What is the sign convention, and does it hold on both sides of the
market?

`tests/test_analysis.py` asserts only properties that hold whatever you decide —
a benchmark price lies inside the book that produced it, a simulation cannot
overfill the parent, an attribution is finite and its keys are stable. Add the
value tests once you have decided; they will be short, and they will be yours.

Write the decisions down here, in this file, before writing the code.

---

## What is already done

The WebSocket client with reconnect and staleness detection, message parsing,
lossless capture, deterministic replay, the Arrow schemas and hour-partitioned
Parquet writer, the validating Polars readers, the latency harness, the plots,
structured logging and the CLI. Reasoning for each is in the module docstrings
and summarised in the README's design notes.
