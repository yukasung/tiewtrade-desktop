"""What a Paper Fill is called, and the candle it is allowed to be read from.

Both markets answer these two the same way, and both must: a Fill id that a
Spot Session and a Futures Session could each mint differently is a record
that cannot be traced back to one rule, and a candle checked against the
rules in one executor but not the other would let a Session fill on a symbol
it does not trade.
"""

from uuid import UUID

from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.symbol_rules import SymbolRules


def paper_fill_id(session_id: UUID, order_id: str) -> str:
    """The id a Paper Fill is recorded under.

    Named `paper` at the front because the source is part of what makes it
    unique: nothing an Exchange returns can collide with a simulated Fill.
    """
    return f"paper:{session_id}:{order_id}:fill"


def require_matching_symbol(candle: Candle, symbol_rules: SymbolRules) -> None:
    """Refuse a candle from a symbol other than the one being traded.

    Rounding a price by another symbol's tick size would produce a Fill that
    looks ordinary and is wrong, so this fails rather than filling.
    """
    if candle.symbol != symbol_rules.symbol:
        raise ValueError(
            "candle symbol must match SymbolRules.symbol: "
            f"candle={candle.symbol!r}, rules={symbol_rules.symbol!r}"
        )
