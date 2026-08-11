import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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
    fee: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class PaperSpotExitFill:
    order_id: str
    fill_id: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    filled_at: datetime


class PaperSpotExecutor:
    def __init__(self, session: SessionConfig, symbol_rules: SymbolRules) -> None:
        if session.spot_policy is None:
            raise ValueError("spot_policy is required for Paper Spot execution")

        self._session = session
        self._symbol_rules = symbol_rules
        self._capital_plan = SpotCapitalPlan.from_available(
            session.available_capital,
            session.spot_policy,
            session.entry_policy,
        )

    def fill_entry(
        self, intent: EntryIntent, candle: Candle
    ) -> PaperSpotEntryFill | None:
        self._require_matching_symbol(candle)

        price = self._symbol_rules.ceil_price(
            candle.open * (Decimal("1") + self._session.slippage_bps / Decimal("10000"))
        )
        quantity = self._symbol_rules.floor_quantity(
            self._capital_plan.entry_notional / price
        )
        if not self._symbol_rules.meets_min_notional(price=price, quantity=quantity):
            self._log_entry_rejected(price=price, quantity=quantity)
            return None

        notional = price * quantity
        order_id = f"entry:{intent.intent_id}"
        return PaperSpotEntryFill(
            order_id=order_id,
            fill_id=f"paper:{self._session.session_id}:{order_id}:fill",
            intent_id=intent.intent_id,
            price=price,
            quantity=quantity,
            fee=notional * self._session.fee_rate,
            filled_at=candle.open_time,
        )

    def fill_take_profit(
        self, basket: Basket, candle: Candle
    ) -> PaperSpotExitFill | None:
        self._require_matching_symbol(candle)

        if basket.take_profit_price is None or candle.high < basket.take_profit_price:
            return None

        price = self._symbol_rules.floor_price(
            basket.take_profit_price
            * (Decimal("1") - self._session.slippage_bps / Decimal("10000"))
        )
        if price <= 0:
            return None

        quantity = basket.total_quantity
        notional = price * quantity
        order_id = f"take-profit:{basket.basket_id}"
        return PaperSpotExitFill(
            order_id=order_id,
            fill_id=f"paper:{self._session.session_id}:{order_id}:fill",
            price=price,
            quantity=quantity,
            fee=notional * self._session.fee_rate,
            filled_at=candle.close_time,
        )

    def fill_stop_loss(
        self, basket: Basket, candle: Candle
    ) -> PaperSpotExitFill | None:
        self._require_matching_symbol(candle)

        if basket.stop_loss_price is None or candle.low > basket.stop_loss_price:
            return None

        price = self._symbol_rules.floor_price(
            basket.stop_loss_price
            * (Decimal("1") - self._session.slippage_bps / Decimal("10000"))
        )
        if price <= 0:
            return None

        quantity = basket.total_quantity
        notional = price * quantity
        order_id = f"stop-loss:{basket.basket_id}"
        return PaperSpotExitFill(
            order_id=order_id,
            fill_id=f"paper:{self._session.session_id}:{order_id}:fill",
            price=price,
            quantity=quantity,
            fee=notional * self._session.fee_rate,
            filled_at=candle.close_time,
        )

    def _log_entry_rejected(self, *, price: Decimal, quantity: Decimal) -> None:
        """Surface a rejected entry; staying silent here looks like a dead bot."""
        rules = self._symbol_rules
        budget = self._capital_plan.entry_notional
        one_lot_cost = rules.step_size * price
        try:
            _LOGGER.warning(
                "%s: per-entry budget %s cannot afford one %s lot (costs %s at price"
                " %s) - raise Available Capital or lower Max Entries",
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

    def _require_matching_symbol(self, candle: Candle) -> None:
        if candle.symbol != self._symbol_rules.symbol:
            raise ValueError(
                "candle symbol must match SymbolRules.symbol: "
                f"candle={candle.symbol!r}, rules={self._symbol_rules.symbol!r}"
            )
