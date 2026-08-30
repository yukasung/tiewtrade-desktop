from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tiewtrade.application.paper_spot_session import PaperSpotSessionIdentity
from tiewtrade.execution.paper_spot import PaperSpotEntryFill, PaperSpotExitFill
from tiewtrade.integrations.sqlite.paper_entry_exit import record_paper_entry_exit
from tiewtrade.integrations.sqlite.paper_fill_record import paper_fill_record
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.basket import ClosedBasket, ClosedEntry
from tiewtrade.trading.trade_history import BasketStatus, FillSide


@dataclass(frozen=True, slots=True)
class PaperSpotHistoryContext:
    session_id: UUID
    symbol: str
    timeframe: str
    preset_version: str

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
        entry_group_id: int,
        group_take_profit_price: Decimal,
    ) -> bool:
        normalized_fill = paper_fill_record(
            fill,
            basket_id=basket_id,
            session_id=self._context.session_id,
            side=FillSide.BUY,
            entry_number=entry_number,
            entry_atr=entry_atr,
            entry_group_id=entry_group_id,
            group_take_profit_price=group_take_profit_price,
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

        Spot Baskets carry no leverage, so None is what its row has always
        held and what this write leaves it holding.
        """
        return record_paper_entry_exit(
            self._store,
            basket_id=basket_id,
            session_id=self._context.session_id,
            fill=fill,
            side=FillSide.SELL,
            leverage=None,
            closed=closed,
            closed_basket=closed_basket,
        )

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

        # The Fill is worth what this sale sold, not what the Basket made over
        # its life: an Entry the trader sold earlier already has a Fill of its
        # own carrying its result, and repeating it here would show the same
        # money twice in the Fills table. The Basket row keeps the whole round.
        normalized_fill = paper_fill_record(
            fill,
            basket_id=basket_id,
            session_id=self._context.session_id,
            side=FillSide.SELL,
            realized_pnl=closed.exit_realized_pnl,
        )
        basket = replace(
            existing,
            closed_at_utc=closed.closed_at,
            entry_count=closed.entry_count,
            realized_pnl=closed.realized_pnl,
            status=BasketStatus.CLOSED,
        )
        return self._store.record_closed_basket(basket, normalized_fill)

    def record_cooldown_waiver(
        self,
        *,
        basket_id: UUID,
        waived_pair: int,
        waived_at: datetime,
    ) -> None:
        self._store.record_cooldown_waiver(basket_id, waived_pair, waived_at)
