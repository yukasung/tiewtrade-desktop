from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from tiewtrade.application.trade_history import (
    TradeHistoryFilter,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000301")
BASKET_ID = UUID("00000000-0000-0000-0000-000000000302")


def test_sqlite_history_serves_application_query_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trade-history.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    opened_at = datetime(2026, 3, 1, tzinfo=UTC)
    closed_at = datetime(2026, 3, 2, tzinfo=UTC)
    closed = BasketResult(
        basket_id=BASKET_ID,
        session_id=SESSION_ID,
        trade_mode=TradeMode.PAPER,
        market_type=MarketType.SPOT,
        symbol="BTCUSDT",
        timeframe="5m",
        strategy_preset_version="rsi-step-grid-v1",
        opened_at_utc=opened_at,
        closed_at_utc=closed_at,
        entry_count=1,
        invested_notional=Decimal("100.000000000000000001"),
        realized_pnl=Decimal("9.800000000000000001"),
        status=BasketStatus.CLOSED,
    )
    opened = replace(
        closed,
        closed_at_utc=None,
        realized_pnl=Decimal("-0.1"),
        status=BasketStatus.OPEN,
    )
    buy = trade_fill(
        "buy-fill",
        side=FillSide.BUY,
        filled_at_utc=opened_at,
        realized_pnl=Decimal("0"),
    )
    sell = trade_fill(
        "sell-fill",
        side=FillSide.SELL,
        filled_at_utc=closed_at,
        realized_pnl=closed.realized_pnl,
    )
    history.record_open_basket(opened, buy)
    history.record_closed_basket(closed, sell)

    reopened = SQLiteTradeHistory(SQLiteDatabase(path))
    page = reopened.list_basket_history(
        TradeHistoryFilter(symbol="BTCUSDT", status=BasketStatus.CLOSED)
    )

    assert page.items == (closed,)
    assert page.realized_pnl == Decimal("9.800000000000000001")
    assert reopened.list_fills(BASKET_ID) == (buy, sell)


def trade_fill(
    fill_id: str,
    *,
    side: FillSide,
    filled_at_utc: datetime,
    realized_pnl: Decimal,
) -> TradeFill:
    return TradeFill(
        fill_id=fill_id,
        basket_id=BASKET_ID,
        session_id=SESSION_ID,
        order_id=f"order-{fill_id}",
        exchange_trade_id=None,
        side=side,
        entry_number=1 if side is FillSide.BUY else None,
        filled_at_utc=filled_at_utc,
        price=Decimal("100"),
        quantity=Decimal("1"),
        notional=Decimal("100"),
        realized_pnl=realized_pnl,
        source=FillSource.PAPER_EXECUTOR,
    )
