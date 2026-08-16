from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from uuid import UUID

from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)

FindOpenBasket = Callable[[UUID], BasketResult | None]
ListFills = Callable[[UUID], tuple[TradeFill, ...]]
RecordClosedBasket = Callable[[BasketResult, TradeFill], object]


class ClosePaperPosition:
    def __init__(
        self,
        *,
        find_open_basket: FindOpenBasket,
        list_fills: ListFills,
        record_closed: RecordClosedBasket,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._find_open_basket = find_open_basket
        self._list_fills = list_fills
        self._record_closed = record_closed
        self._clock = clock

    def execute(
        self,
        session: ConfiguredPaperSession,
        *,
        market_price: Callable[[], Decimal],
        tick_size: Decimal,
    ) -> None:
        if session.config.trade_mode is not TradeMode.PAPER:
            raise ValueError("manual close requires a Paper Session")
        if not tick_size.is_finite() or tick_size <= 0:
            raise ValueError("tick_size must be finite and positive")
        basket = self._find_open_basket(session.config.session_id)
        if basket is None:
            return
        if basket.session_id != session.config.session_id:
            raise ValueError("open Basket belongs to a different Session")

        entries = tuple(
            fill
            for fill in self._list_fills(basket.basket_id)
            if fill.entry_number is not None
        )
        if not entries:
            raise ValueError("open Basket requires Entry Fills")
        execution_price = market_price()
        if not execution_price.is_finite() or execution_price <= 0:
            raise ValueError("market_price must be finite and positive")
        long_position = basket.market_type is MarketType.SPOT or (
            entries[0].side is FillSide.BUY
        )
        exit_price = (execution_price / tick_size).to_integral_value(
            rounding=ROUND_DOWN if long_position else ROUND_CEILING
        ) * tick_size
        quantity = sum((fill.quantity for fill in entries), Decimal("0"))
        realized_pnl = sum(
            (
                (exit_price - fill.price) * fill.quantity
                if long_position
                else (fill.price - exit_price) * fill.quantity
                for fill in entries
            ),
            start=Decimal("0"),
        )
        closed_at = self._clock()
        order_id = f"manual-close:{basket.basket_id}"
        fill = TradeFill(
            fill_id=f"paper:{session.config.session_id}:{order_id}:fill",
            basket_id=basket.basket_id,
            session_id=session.config.session_id,
            order_id=order_id,
            exchange_trade_id=None,
            side=FillSide.SELL if long_position else FillSide.BUY,
            entry_number=None,
            filled_at_utc=closed_at,
            price=exit_price,
            quantity=quantity,
            notional=exit_price * quantity,
            realized_pnl=realized_pnl,
            source=FillSource.PAPER_EXECUTOR,
        )
        closed = replace(
            basket,
            closed_at_utc=closed_at,
            realized_pnl=realized_pnl,
            status=BasketStatus.CLOSED,
        )
        recorded = self._record_closed(closed, fill)
        if recorded is False:
            raise RuntimeError("Paper position close was not recorded")
