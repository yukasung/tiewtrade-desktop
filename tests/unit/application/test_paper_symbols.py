from decimal import Decimal

import pytest

from tiewtrade.application.paper_symbols import (
    PAPER_SYMBOLS,
    paper_symbol_rules,
    validate_paper_symbol,
)
from tiewtrade.trading.session_config import MarketType


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
def test_paper_symbol_rules_support_the_allowlist(symbol: str) -> None:
    assert paper_symbol_rules(symbol, MarketType.SPOT).symbol == symbol
    assert validate_paper_symbol(symbol) == symbol


def test_paper_symbol_rules_carry_the_published_exchange_filters() -> None:
    rules = paper_symbol_rules("BTCUSDT", MarketType.SPOT)

    assert rules.tick_size == Decimal("0.01")
    assert rules.step_size == Decimal("0.00001")
    assert rules.min_notional == Decimal("5")


@pytest.mark.parametrize(
    ("symbol", "step_size"),
    [("BTCUSDT", "0.00001"), ("ETHUSDT", "0.0001"), ("BNBUSDT", "0.001")],
)
def test_each_market_carries_its_own_lot_step(symbol: str, step_size: str) -> None:
    """A step is a slice of the asset, and the three are not worth the same."""
    assert paper_symbol_rules(symbol, MarketType.SPOT).step_size == Decimal(step_size)


@pytest.mark.parametrize("market_type", list(MarketType))
def test_both_market_types_read_the_same_published_filters(
    market_type: MarketType,
) -> None:
    assert paper_symbol_rules("BTCUSDT", market_type) == paper_symbol_rules(
        "BTCUSDT", MarketType.SPOT
    )


def test_paper_symbol_rules_reject_unknown_symbol() -> None:
    with pytest.raises(ValueError, match="Paper symbol is not supported"):
        paper_symbol_rules("BTCTHB", MarketType.SPOT)


def test_paper_symbols_are_the_supported_ui_allowlist() -> None:
    assert PAPER_SYMBOLS == ("BTCUSDT", "ETHUSDT", "BNBUSDT")
