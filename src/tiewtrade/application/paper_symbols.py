from decimal import Decimal

from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules

PAPER_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")

_SPOT_RULES = {
    symbol: SymbolRules(
        symbol=symbol,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    for symbol in PAPER_SYMBOLS
}
_FUTURES_RULES = {
    symbol: SymbolRules(
        symbol=symbol,
        tick_size=Decimal("0.1") if symbol == "BTCUSDT" else Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    for symbol in PAPER_SYMBOLS
}


def validate_paper_symbol(symbol: str) -> str:
    if symbol not in PAPER_SYMBOLS:
        raise ValueError("Paper symbol is not supported")
    return symbol


def paper_symbol_rules(symbol: str, market_type: MarketType) -> SymbolRules:
    validate_paper_symbol(symbol)
    return (
        _SPOT_RULES[symbol]
        if market_type is MarketType.SPOT
        else _FUTURES_RULES[symbol]
    )
