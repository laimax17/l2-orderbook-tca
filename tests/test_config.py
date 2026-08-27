from __future__ import annotations

import pytest

from l2tca.config import FeedConfig, normalize_symbol, symbol_to_path_token


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("XBT/USD", "BTC/USD"),
        ("xbt/usd", "BTC/USD"),
        ("BTC/USD", "BTC/USD"),
        ("ETH/USD", "ETH/USD"),
    ],
)
def test_normalize_symbol_maps_v1_spelling(given: str, expected: str) -> None:
    assert normalize_symbol(given) == expected


def test_symbol_to_path_token_is_filesystem_safe() -> None:
    assert symbol_to_path_token("XBT/USD") == "BTC-USD"
    assert "/" not in symbol_to_path_token("ETH/EUR")


def test_config_rejects_a_depth_kraken_does_not_serve() -> None:
    with pytest.raises(ValueError, match="depth must be one of"):
        FeedConfig(depth=42)


def test_config_rejects_a_backoff_that_never_grows() -> None:
    with pytest.raises(ValueError, match="multiplier"):
        FeedConfig(backoff_multiplier=1.0)


def test_wire_symbol_is_normalised() -> None:
    assert FeedConfig(symbol="xbt/usd").wire_symbol == "BTC/USD"
