from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from tiewtrade.application.paper_futures_session import PaperFuturesSessionIdentity
from tiewtrade.execution.paper_futures import (
    PaperFuturesEntryFill,
    PaperFuturesExitFill,
)
from tiewtrade.integrations.sqlite.paper_entry_exit import record_paper_entry_exit
from tiewtrade.integrations.sqlite.paper_fill_record import paper_fill_record
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.basket import ClosedBasket, ClosedEntry
from tiewtrade.trading.position import PositionSide
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketResult, BasketStatus, FillSide

PAPER_FUTURES_FUNDING_FEE = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class PaperFuturesHistoryContext:
    session_id: UUID
    symbol: str
    timeframe: str
    preset_version: str
    leverage: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.leverage, bool)
            or not isinstance(self.leverage, int)
            or not 1 <= self.leverage <= 5
        ):
            raise ValueError("leverage must be between 1 and 5")

    @property
    def session_identity(self) -> PaperFuturesSessionIdentity:
        return PaperFuturesSessionIdentity(
            session_id=self.session_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            preset_version=self.preset_version,
            leverage=self.leverage,
        )


class PaperFuturesSQLiteHistory:
    def __init__(
        self,
        context: PaperFuturesHistoryContext,
        store: SQLiteTradeHistory,
    ) -> None:
        self._context = context
        self._store = store

    @property
    def session_identity(self) -> PaperFuturesSessionIdentity:
        return self._context.session_identity

    def record_entry(
        self,
        *,
        basket_id: UUID,
        entry_number: int,
        fill: PaperFuturesEntryFill,
    ) -> bool:
        normalized_fill = paper_fill_record(
            fill,
            basket_id=basket_id,
            session_id=self._context.session_id,
            side=_entry_fill_side(fill.side),
            entry_number=entry_number,
        )
        existing = self._store.get_basket(basket_id)
        if existing is None:
            basket = BasketResult(
                basket_id=basket_id,
                session_id=self._context.session_id,
                trade_mode=TradeMode.PAPER,
                market_type=MarketType.FUTURES,
                symbol=self._context.symbol,
                timeframe=self._context.timeframe,
                strategy_preset_version=self._context.preset_version,
                opened_at_utc=fill.filled_at,
                closed_at_utc=None,
                entry_count=1,
                invested_notional=normalized_fill.notional,
                realized_pnl=Decimal("0"),
                status=BasketStatus.OPEN,
                leverage=self._context.leverage,
            )
            return self._store.record_open_basket(basket, normalized_fill)

        order_already_filled = any(
            item.order_id == normalized_fill.order_id
            for item in self._store.list_fills(basket_id)
        )
        basket = replace(
            existing,
            leverage=self._context.leverage,
            entry_count=(
                existing.entry_count
                if order_already_filled
                else existing.entry_count + 1
            ),
            invested_notional=existing.invested_notional + normalized_fill.notional,
        )
        return self._store.record_entry_fill(basket, normalized_fill)

    def record_entry_exit(
        self,
        *,
        basket_id: UUID,
        fill: PaperFuturesExitFill,
        closed: ClosedEntry,
        closed_basket: ClosedBasket | None,
    ) -> bool:
        """Record one Entry sold at a target, whichever target took it.

        Written the same way Paper Spot writes it, so a Fill row cannot mean
        one thing in one market and another in the other. What this market
        adds is the side the Position was held on, which its Fills carry and
        Spot's never do, and the leverage its Basket row is pinned to.
        """
        if closed_basket is not None:
            if closed_basket.basket_id != basket_id:
                raise ValueError("closed Basket does not match basket_id")
            if closed_basket.position_side is not fill.side:
                raise ValueError("closed Basket and exit Fill use different sides")

        return record_paper_entry_exit(
            self._store,
            basket_id=basket_id,
            session_id=self._context.session_id,
            fill=fill,
            side=_exit_fill_side(fill.side),
            leverage=self._context.leverage,
            closed=closed,
            closed_basket=closed_basket,
        )


def _entry_fill_side(side: PositionSide) -> FillSide:
    if side is PositionSide.LONG:
        return FillSide.BUY
    return FillSide.SELL


def _exit_fill_side(side: PositionSide) -> FillSide:
    if side is PositionSide.LONG:
        return FillSide.SELL
    return FillSide.BUY
