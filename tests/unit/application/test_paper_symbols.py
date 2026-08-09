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


def test_paper_futures_rules_keep_btcusdt_tick_size() -> None:
    assert paper_symbol_rules("BTCUSDT", MarketType.FUTURES).tick_size == Decimal("0.1")


def test_paper_symbol_rules_reject_unknown_symbol() -> None:
    with pytest.raises(ValueError, match="Paper symbol is not supported"):
        paper_symbol_rules("DOGEUSDT", MarketType.SPOT)


def test_paper_symbols_are_the_supported_ui_allowlist() -> None:
    assert PAPER_SYMBOLS == ("BTCUSDT", "ETHUSDT", "BNBUSDT")
