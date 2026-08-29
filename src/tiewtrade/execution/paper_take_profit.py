"""Which Entries a target sells, and what each one is filled at.

Which Entries leave is a Basket rule and belongs to `trading`; what a Paper
Fill is worth is an execution rule and belongs here. Both markets answer the
second one the same way and both must: an exit price rounds against the
seller, so a Paper Fill can never flatter itself, and a rounding read one way
in one executor and another way in the other would let the same candle pay
differently depending on which market watched it.

What is left to each executor is only the Fill it books — its own record type,
its own order id, and the moment it says the sale happened.
"""

from dataclasses import dataclass
from decimal import Decimal

from tiewtrade.execution.paper_fill_identity import require_matching_symbol
from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.basket import Basket
from tiewtrade.trading.position import PositionSide
from tiewtrade.trading.symbol_rules import SymbolRules


@dataclass(frozen=True, slots=True)
class PaperTakeProfitSale:
    """One Entry a candle carried to a target, priced as it would fill.

    The price is already rounded to the Symbol's tick, so an executor turning
    this into a Fill has nothing left to decide about what the sale was worth.
    """

    entry_number: int
    quantity: Decimal
    price: Decimal


def paper_take_profit_sales(
    basket: Basket,
    candle: Candle,
    symbol_rules: SymbolRules,
) -> tuple[PaperTakeProfitSale, ...]:
    """Every Entry this candle sells, and the price each one sells at.

    The Basket decides which Entries those are and what target each leaves at,
    which is the target of the Group each one is in. One sale per Entry, so a
    candle that reaches two Groups says what each of them sold at rather than
    averaging two sales into a figure neither of them made.
    """
    require_matching_symbol(candle, symbol_rules)

    return tuple(
        PaperTakeProfitSale(
            entry_number=exit.entry.entry_number,
            quantity=exit.entry.quantity,
            price=_rounded_against_the_seller(exit.price, basket, symbol_rules),
        )
        for exit in basket.take_profit_exits(candle.high, candle.low)
    )


def _rounded_against_the_seller(
    price: Decimal,
    basket: Basket,
    symbol_rules: SymbolRules,
) -> Decimal:
    """The tick a sale lands on, always the one the seller likes less.

    A Long sells at or below the target it reached and a Short at or above it,
    because a Paper Fill that rounded the other way would book a price the
    market was never asked for.
    """
    if basket.position_side is PositionSide.LONG:
        return symbol_rules.floor_price(price)
    return symbol_rules.ceil_price(price)
