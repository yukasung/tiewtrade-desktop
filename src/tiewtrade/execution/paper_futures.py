from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tiewtrade.execution.paper_fill_identity import (
    paper_fill_id,
    require_matching_symbol,
)
from tiewtrade.execution.paper_take_profit import (
    PaperTakeProfitSale,
    paper_take_profit_sales,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.trading.basket import Basket, BasketCloseReason
from tiewtrade.trading.capital import FuturesCapitalPlan
from tiewtrade.trading.position import PositionSide
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules


@dataclass(frozen=True, slots=True)
class PaperFuturesEntryFill:
    order_id: str
    fill_id: str
    intent_id: str
    side: PositionSide
    price: Decimal
    quantity: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class PaperFuturesExitFill:
    order_id: str
    fill_id: str
    side: PositionSide
    close_reason: BasketCloseReason
    price: Decimal
    quantity: Decimal
    filled_at: datetime
    # The Entry this Fill closed. Every exit this market makes closes one
    # Entry: a Pair that reached its own target leaves at that price, and a
    # Basket that reached its own sells what is left one Entry at a time, so
    # each sale says which Entry it was and what that Entry got.
    entry_number: int


class PaperFuturesExecutor:
    def __init__(self, session: SessionConfig, symbol_rules: SymbolRules) -> None:
        if (
            session.trade_mode is not TradeMode.PAPER
            or session.market_type is not MarketType.FUTURES
            or session.futures_policy is None
        ):
            raise ValueError(
                "PaperFuturesExecutor requires a Paper Futures configuration"
            )

        self._session = session
        self._symbol_rules = symbol_rules
        self._capital_plan = FuturesCapitalPlan.from_capital_per_entry(
            session.capital_per_entry,
            session.futures_policy,
        )

    def fill_entry(
        self, intent: EntryIntent, candle: Candle
    ) -> PaperFuturesEntryFill | None:
        require_matching_symbol(candle, self._symbol_rules)
        if intent.side is PositionSide.LONG:
            price = self._symbol_rules.ceil_price(candle.open)
        else:
            price = self._symbol_rules.floor_price(candle.open)

        quantity = self._symbol_rules.floor_quantity(
            self._capital_plan.target_notional_per_entry / price
        )
        if not self._symbol_rules.meets_min_notional(price=price, quantity=quantity):
            return None

        order_id = f"entry:{intent.intent_id}"
        return PaperFuturesEntryFill(
            order_id=order_id,
            fill_id=paper_fill_id(self._session.session_id, order_id),
            intent_id=intent.intent_id,
            side=intent.side,
            price=price,
            quantity=quantity,
            filled_at=candle.open_time,
        )

    def fill_take_profit(
        self, basket: Basket, candle: Candle
    ) -> tuple[PaperFuturesExitFill, ...]:
        """A Fill for every Entry this candle carried to a target.

        Which Entries those are and what each is worth is read once, the same
        way Paper Spot reads it; all that is left here is the record Futures
        keeps of each sale — its side, why it sold, and the candle close it
        books at, this market never selling between candles.
        """
        return tuple(
            self._take_profit_fill(basket, sale, filled_at=candle.close_time)
            for sale in paper_take_profit_sales(basket, candle, self._symbol_rules)
        )

    def _take_profit_fill(
        self,
        basket: Basket,
        sale: PaperTakeProfitSale,
        *,
        filled_at: datetime,
    ) -> PaperFuturesExitFill:
        close_reason = BasketCloseReason.TAKE_PROFIT
        order_id = f"{close_reason.value}:{basket.basket_id}:{sale.entry_number}"
        return PaperFuturesExitFill(
            order_id=order_id,
            fill_id=paper_fill_id(self._session.session_id, order_id),
            side=basket.position_side,
            close_reason=close_reason,
            price=sale.price,
            quantity=sale.quantity,
            filled_at=filled_at,
            entry_number=sale.entry_number,
        )
