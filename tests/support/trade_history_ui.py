from decimal import Decimal

from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)


def empty_basket_history(filters: TradeHistoryFilter) -> BasketHistoryResult:
    """A store with no Baskets, for tests that only need the window to compose."""
    del filters
    return BasketHistoryResult(items=(), net_realized_pnl=Decimal("0"))


def empty_trade_fills(
    filters: TradeHistoryFilter,
) -> tuple[TradeFillHistoryItem, ...]:
    del filters
    return ()
