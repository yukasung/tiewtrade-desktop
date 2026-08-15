from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from tiewtrade.application.paper_spot_session import PaperSpotSessionIdentity
from tiewtrade.execution.paper_spot import PaperSpotEntryFill, PaperSpotExitFill
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.basket import ClosedBasket, ClosedEntry
from tiewtrade.trading.trade_history import (
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)


@dataclass(frozen=True, slots=True)
class PaperSpotHistoryContext:
    session_id: UUID
    symbol: str
    timeframe: str
    preset_version: str
    commission_asset: str

    @property
    def session_identity(self) -> PaperSpotSessionIdentity:
        return PaperSpotSessionIdentity(
            session_id=self.session_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            preset_version=self.preset_version,
        )


class PaperSpotSQLiteHistory:
    def __init__(
        self,
        context: PaperSpotHistoryContext,
        store: SQLiteTradeHistory,
    ) -> None:
        self._context = context
        self._store = store

    @property
    def session_identity(self) -> PaperSpotSessionIdentity:
        return self._context.session_identity

    def record_entry(
        self,
        *,
        basket_id: UUID,
        entry_number: int,
        fill: PaperSpotEntryFill,
        entry_atr: Decimal,
    ) -> bool:
        normalized_fill = TradeFill(
            fill_id=fill.fill_id,
            basket_id=basket_id,
            session_id=self._context.session_id,
            order_id=fill.order_id,
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=entry_number,
            filled_at_utc=fill.filled_at,
            price=fill.price,
            quantity=fill.quantity,
            notional=fill.price * fill.quantity,
            commission=fill.fee,
            commission_asset=self._context.commission_asset,
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
            entry_atr=entry_atr,
        )
        return self._store.record_paper_spot_entry_fill(
            normalized_fill,
            symbol=self._context.symbol,
            timeframe=self._context.timeframe,
            strategy_preset_version=self._context.preset_version,
        )

    def record_entry_exit(
        self,
        *,
        basket_id: UUID,
        fill: PaperSpotExitFill,
        closed: ClosedEntry,
        closed_basket: ClosedBasket | None,
    ) -> bool:
        """Record one Entry sold at its own target.

        The Basket's running total grows by what this Entry realised, and the
        Basket itself closes only when the sale was of its last Entry.
        """
        existing = self._store.get_basket(basket_id)
        if existing is None:
            raise ValueError("Basket does not exist")

        normalized_fill = TradeFill(
            fill_id=fill.fill_id,
            basket_id=basket_id,
            session_id=self._context.session_id,
            order_id=fill.order_id,
            exchange_trade_id=None,
            side=FillSide.SELL,
            entry_number=None,
            filled_at_utc=fill.filled_at,
            price=fill.price,
            quantity=fill.quantity,
            notional=fill.price * fill.quantity,
            commission=fill.fee,
            commission_asset=self._context.commission_asset,
            realized_pnl=closed.net_realized_pnl,
            source=FillSource.PAPER_EXECUTOR,
            closes_entry_number=closed.entry_number,
        )
        if closed_basket is not None:
            basket = replace(
                existing,
                closed_at_utc=closed_basket.closed_at,
                entry_count=closed_basket.entry_count,
                gross_realized_pnl=closed_basket.gross_realized_pnl,
                trading_fees=closed_basket.trading_fees,
                funding_fee=closed_basket.funding_fee,
                net_realized_pnl=closed_basket.net_realized_pnl,
                status=BasketStatus.CLOSED,
            )
            return self._store.record_closed_basket(basket, normalized_fill)

        gross_realized_pnl = existing.gross_realized_pnl + closed.gross_realized_pnl
        trading_fees = existing.trading_fees + fill.fee
        basket = replace(
            existing,
            gross_realized_pnl=gross_realized_pnl,
            trading_fees=trading_fees,
            net_realized_pnl=(gross_realized_pnl - trading_fees - existing.funding_fee),
        )
        return self._store.record_entry_exit_fill(basket, normalized_fill)

    def record_close(
        self,
        *,
        basket_id: UUID,
        fill: PaperSpotExitFill,
        closed: ClosedBasket,
    ) -> bool:
        existing = self._store.get_basket(basket_id)
        if existing is None:
            raise ValueError("Basket does not exist")

        normalized_fill = TradeFill(
            fill_id=fill.fill_id,
            basket_id=basket_id,
            session_id=self._context.session_id,
            order_id=fill.order_id,
            exchange_trade_id=None,
            side=FillSide.SELL,
            entry_number=None,
            filled_at_utc=fill.filled_at,
            price=fill.price,
            quantity=fill.quantity,
            notional=fill.price * fill.quantity,
            commission=fill.fee,
            commission_asset=self._context.commission_asset,
            realized_pnl=closed.net_realized_pnl,
            source=FillSource.PAPER_EXECUTOR,
        )
        basket = replace(
            existing,
            closed_at_utc=closed.closed_at,
            entry_count=closed.entry_count,
            gross_realized_pnl=closed.gross_realized_pnl,
            trading_fees=closed.trading_fees,
            funding_fee=closed.funding_fee,
            net_realized_pnl=closed.net_realized_pnl,
            status=BasketStatus.CLOSED,
        )
        return self._store.record_closed_basket(basket, normalized_fill)
