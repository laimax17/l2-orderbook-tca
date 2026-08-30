# Test fixtures

## `sample.jsonl.gz` — a recorded Kraken session

A trimmed capture of the live `book` channel. Several tests read it and skip
when it is absent, because a capture is data, not source: it cannot be
generated, only recorded.

Three things only a real capture establishes, and the synthetic generator
(`l2tca synth`) cannot:

* **Checksums.** Synthetic frames carry none on purpose — fabricating one would
  need the very CRC32 implementation it is meant to validate. This file is the
  only place `verify_checksum` is proven against something other than itself.
* **Real depth behaviour.** Levels leaving and re-entering the depth window,
  frames touching both sides at once, bursts and quiet stretches.
* **Real inter-arrival timing.** What the staleness watchdog and the benchmark
  percentiles are tuned against.

### Replacing it

Keep it small: it is committed source, so it should be a few hundred kilobytes
compressed, and long enough to hold the opening snapshot plus a few thousand
updates.

```bash
mkdir -p tests/fixtures
uv run l2tca record --symbol BTC/USD --depth 100 --duration 600
head -n 5000 data/raw/kraken_book_*.jsonl > tests/fixtures/sample.jsonl
gzip tests/fixtures/sample.jsonl
uv run l2tca inspect tests/fixtures/sample.jsonl.gz
```

`head -n` keeps the header and the opening snapshot, which is what the tests
need. Plain `.jsonl` is accepted too; `.gz` is preferred because book JSONL
compresses about tenfold.

The full ten-minute capture stays out of git — `data/raw/` is ignored — and is
what `l2tca bench` should run against, where more data is strictly better.

See `docs/CORE.md` for the longer version.
