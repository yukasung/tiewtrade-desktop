"""The markets a Paper Session may be opened on, and how each one trades.

Binance TH is where these prices come from, so these are its markets and
its filters — a tick size, a lot step and a minimum notional the exchange
publishes per symbol. The lot step is the one that differs between them,
because a step is a slice of the asset itself and the three are not worth
the same.

Spot and Futures read the same table. Binance TH lists no Futures market,
so there is no second set of filters to differ from: inventing one would
only put a number here that no exchange ever published.
"""

from decimal import Decimal

from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules

PAPER_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")

_TICK_SIZE = Decimal("0.01")
_MIN_NOTIONAL = Decimal("5")
_RULES = {
    "BTCUSDT": SymbolRules(
        symbol="BTCUSDT",
        tick_size=_TICK_SIZE,
        step_size=Decimal("0.00001"),
        min_notional=_MIN_NOTIONAL,
    ),
    "ETHUSDT": SymbolRules(
        symbol="ETHUSDT",
        tick_size=_TICK_SIZE,
        step_size=Decimal("0.0001"),
        min_notional=_MIN_NOTIONAL,
    ),
    "BNBUSDT": SymbolRules(
        symbol="BNBUSDT",
        tick_size=_TICK_SIZE,
        step_size=Decimal("0.001"),
        min_notional=_MIN_NOTIONAL,
    ),
}


def validate_paper_symbol(symbol: str) -> str:
    if symbol not in PAPER_SYMBOLS:
        raise ValueError("Paper symbol is not supported")
    return symbol


def paper_symbol_rules(symbol: str, market_type: MarketType) -> SymbolRules:
    validate_paper_symbol(symbol)
    del market_type
    return _RULES[symbol]
