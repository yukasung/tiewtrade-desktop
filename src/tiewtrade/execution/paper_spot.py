import logging
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
from tiewtrade.trading.basket import Basket
from tiewtrade.trading.capital import SpotCapitalPlan
from tiewtrade.trading.session_config import SessionConfig
from tiewtrade.trading.symbol_rules import SymbolRules

ENTRY_REJECTED_EVENT = "paper_spot.entry.rejected_below_min_notional"
_LOGGER = logging.getLogger("tiewtrade.execution.paper_spot")


@dataclass(frozen=True, slots=True)
class PaperSpotEntryFill:
    order_id: str
    fill_id: str
    intent_id: str
    price: Decimal
    quantity: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class PaperSpotExitFill:
    order_id: str
    fill_id: str
    price: Decimal
    quantity: Decimal
    filled_at: datetime
    # The Entry this Fill closed, when it closed one rather than the Basket.
    # Only a trader selling one Entry sets it: take profit, stop loss and a
    # manual close of the Position all take everything and leave it None.
    entry_number: int | None = None


class PaperSpotExecutor:
    def __init__(self, session: SessionConfig, symbol_rules: SymbolRules) -> None:
        self._session = session
        self._symbol_rules = symbol_rules
        self._capital_plan = SpotCapitalPlan.from_capital_per_entry(
            session.capital_per_entry
        )

    def fill_entry(
        self, intent: EntryIntent, candle: Candle
    ) -> PaperSpotEntryFill | None:
        require_matching_symbol(candle, self._symbol_rules)

        price = self._symbol_rules.ceil_price(candle.open)
        quantity = self._symbol_rules.floor_quantity(
            self._capital_plan.entry_notional / price
        )
        if not self._symbol_rules.meets_min_notional(price=price, quantity=quantity):
            self._log_entry_rejected(price=price, quantity=quantity)
            return None

        order_id = f"entry:{intent.intent_id}"
        return PaperSpotEntryFill(
            order_id=order_id,
            fill_id=paper_fill_id(self._session.session_id, order_id),
            intent_id=intent.intent_id,
            price=price,
            quantity=quantity,
            filled_at=candle.open_time,
        )

    def fill_take_profit(
        self, basket: Basket, candle: Candle, *, filled_at: datetime
    ) -> tuple[PaperSpotExitFill, ...]:
        """A Fill for every Entry this candle carried to a target.

        Which Entries those are and what each is worth is read once, the same
        way Paper Futures reads it; all that is left here is the record Spot
        keeps of each sale.
        """
        return tuple(
            self._take_profit_fill(basket, sale, filled_at=filled_at)
            for sale in paper_take_profit_sales(basket, candle, self._symbol_rules)
        )

    def _take_profit_fill(
        self,
        basket: Basket,
        sale: PaperTakeProfitSale,
        *,
        filled_at: datetime,
    ) -> PaperSpotExitFill:
        order_id = f"take-profit:{basket.basket_id}:{sale.entry_number}"
        return PaperSpotExitFill(
            order_id=order_id,
            fill_id=paper_fill_id(self._session.session_id, order_id),
            price=sale.price,
            quantity=sale.quantity,
            filled_at=filled_at,
            entry_number=sale.entry_number,
        )

    def fill_entry_close(
        self,
        basket: Basket,
        entry_number: int,
        *,
        price: Decimal,
        filled_at: datetime,
    ) -> PaperSpotExitFill:
        """A Fill for one Entry sold on demand, at the price the market is at.

        The same sale a target makes, at a price nobody promised: the trader
        asked for it, so it books where the market is rather than where the
        Entry was aiming. Rounded the way every sale here is rounded — down,
        against the seller — so a Paper Fill cannot flatter itself.
        """
        entry = next(
            (item for item in basket.entries if item.entry_number == entry_number),
            None,
        )
        if entry is None:
            raise ValueError("basket has no open Entry with that number")
        order_id = f"close-entry:{basket.basket_id}:{entry_number}"
        return PaperSpotExitFill(
            order_id=order_id,
            fill_id=paper_fill_id(self._session.session_id, order_id),
            price=self._symbol_rules.floor_price(price),
            quantity=entry.quantity,
            filled_at=filled_at,
            entry_number=entry_number,
        )

    def fill_stop_loss(
        self, basket: Basket, candle: Candle, *, filled_at: datetime
    ) -> PaperSpotExitFill | None:
        require_matching_symbol(candle, self._symbol_rules)

        if basket.stop_loss_price is None or candle.low > basket.stop_loss_price:
            return None

        price = self._symbol_rules.floor_price(basket.stop_loss_price)

        quantity = basket.total_quantity
        order_id = f"stop-loss:{basket.basket_id}"
        return PaperSpotExitFill(
            order_id=order_id,
            fill_id=paper_fill_id(self._session.session_id, order_id),
            price=price,
            quantity=quantity,
            filled_at=filled_at,
        )

    def _log_entry_rejected(self, *, price: Decimal, quantity: Decimal) -> None:
        """Surface a rejected entry; staying silent here looks like a dead bot."""
        rules = self._symbol_rules
        budget = self._capital_plan.entry_notional
        one_lot_cost = rules.step_size * price
        try:
            _LOGGER.warning(
                "%s: per-entry budget %s cannot afford one %s lot (costs %s at price"
                " %s) - raise the Amount",
                ENTRY_REJECTED_EVENT,
                budget,
                rules.step_size,
                one_lot_cost,
                price,
                extra={
                    "event_name": ENTRY_REJECTED_EVENT,
                    "symbol": rules.symbol,
                    "entry_budget_notional": str(budget),
                    "price": str(price),
                    "quantity_after_lot_rounding": str(quantity),
                    "step_size": str(rules.step_size),
                    "min_notional": str(rules.min_notional),
                    "minimum_affordable_entry_notional": str(one_lot_cost),
                },
            )
        except Exception:
            return
