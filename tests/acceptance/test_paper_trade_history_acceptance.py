import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

import tiewtrade.desktop_main as desktop_main
import tiewtrade.ui.desktop as ui_desktop
from tests.support import paper_trade_history_acceptance
from tests.support.paper_trade_history_acceptance import (
    build_spot_session,
    run_closed_futures,
    run_closed_spot,
    spot_candles,
    spot_history,
)
from tests.support.qt_interactions import click, table_item
from tiewtrade.application.session_persistence import SessionPersistenceBlockedError
from tiewtrade.application.trade_history import TradeHistoryFilter
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.persistent_paper_spot_session import (
    create_persistent_paper_spot_session,
)
from tiewtrade.integrations.sqlite.trade_history import (
    SQLiteTradeHistory,
)
from tiewtrade.integrations.sqlite.trade_history_errors import (
    TradeHistoryUnavailableError,
)
from tiewtrade.trading.trade_history import (
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)
from tiewtrade.ui.main_window import MainWindow
from tiewtrade.ui.trading_workspace import WorkspaceTab


def _show_every_day(window: MainWindow, qtbot: QtBot) -> None:
    """Widen past the day the page opens on, to reach a dated fixture."""
    qtbot.waitUntil(window.trade_history.basket_filters.search_button.isEnabled)
    window.trade_history.basket_filters.from_date.clear_date()
    window.trade_history.basket_filters.to_date.clear_date()
    click(window.trade_history.basket_filters.search_button)


def test_sqlite_failure_blocks_new_paper_entry_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "paper-trade-history.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_trade_fill_insert
            BEFORE INSERT ON trade_fills
            BEGIN
                SELECT RAISE(ABORT, 'forced Trade History failure');
            END;
            """
        )

    session_id = uuid4()
    history = SQLiteTradeHistory(database)
    persistent = create_persistent_paper_spot_session(
        build_spot_session(session_id),
        spot_history(session_id, history),
    )
    candles = spot_candles()

    for _index, candle in enumerate(candles):
        try:
            snapshot = persistent.process_completed_candle(
                candle,
                received_at=candle.close_time,
                backfilled=False,
            )
        except TradeHistoryUnavailableError as raised:
            assert "SQLite write failed" in str(raised)
            break
        assert snapshot.session.entry_fill is None
    else:
        raise AssertionError("deterministic Paper Spot candles did not reach an Entry")

    assert history.list_basket_history(TradeHistoryFilter()).items == ()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0] == 0

    next_candle = candles[_index + 1]
    with pytest.raises(SessionPersistenceBlockedError, match="blocked"):
        persistent.process_completed_candle(
            next_candle,
            received_at=next_candle.close_time,
            backfilled=False,
        )

    reopened_database = SQLiteDatabase(path)
    reopened_database.migrate()
    reopened_history = SQLiteTradeHistory(reopened_database)
    assert reopened_history.list_basket_history(TradeHistoryFilter()).items == ()
    with reopened_database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0] == 0


def test_open_basket_duplicate_and_partial_fills_remain_deterministic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "paper-trade-history.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    history = SQLiteTradeHistory(database)

    closed_basket_id = run_closed_spot(history)
    open_history = paper_trade_history_acceptance.run_spot_until_entry(history)

    assert history.record_open_basket(open_history.basket, open_history.fill) is False

    first_fill = open_history.fill
    partial_fill = TradeFill(
        fill_id=f"{first_fill.fill_id}-partial-2",
        basket_id=first_fill.basket_id,
        session_id=first_fill.session_id,
        order_id=first_fill.order_id,
        exchange_trade_id=first_fill.exchange_trade_id,
        side=first_fill.side,
        entry_number=first_fill.entry_number,
        filled_at_utc=first_fill.filled_at_utc + timedelta(seconds=1),
        price=first_fill.price,
        quantity=Decimal("0.001"),
        notional=first_fill.price * Decimal("0.001"),
        realized_pnl=Decimal("0"),
        source=first_fill.source,
    )
    updated_open_basket = replace(
        open_history.basket,
        entry_count=1,
        invested_notional=(
            open_history.basket.invested_notional + partial_fill.notional
        ),
        realized_pnl=open_history.basket.realized_pnl,
    )

    assert history.record_entry_fill(updated_open_basket, partial_fill) is True
    assert history.record_entry_fill(updated_open_basket, partial_fill) is False

    first_restart_database = SQLiteDatabase(path)
    first_restart_database.migrate()
    first_restart = SQLiteTradeHistory(first_restart_database)
    first_page = first_restart.list_basket_history(TradeHistoryFilter())
    first_open_page = first_restart.list_basket_history(
        TradeHistoryFilter(status=BasketStatus.OPEN)
    )
    first_fills = first_restart.list_fills(open_history.basket.basket_id)

    assert {basket.basket_id for basket in first_page.items} == {
        closed_basket_id,
        open_history.basket.basket_id,
    }
    assert first_page.realized_pnl == Decimal("23.679")
    assert first_open_page.items == (updated_open_basket,)
    assert first_open_page.realized_pnl == Decimal("0")
    assert len(first_fills) == 2
    assert {fill.order_id for fill in first_fills} == {first_fill.order_id}
    assert {fill.entry_number for fill in first_fills} == {1}
    assert first_fills == (first_fill, partial_fill)

    second_restart_database = SQLiteDatabase(path)
    second_restart_database.migrate()
    second_restart = SQLiteTradeHistory(second_restart_database)
    second_page = second_restart.list_basket_history(TradeHistoryFilter())
    second_fills = second_restart.list_fills(open_history.basket.basket_id)

    assert second_page == first_page
    assert second_fills == first_fills


def test_paper_execution_history_survives_restart_and_reaches_desktop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
) -> None:
    path = tmp_path / "paper-trade-history.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    _block_network(monkeypatch)

    spot_basket_id = run_closed_spot(history)
    futures_basket_id = run_closed_futures(history)
    before_restart = history.list_basket_history(TradeHistoryFilter())
    before_fills = {
        basket.basket_id: history.list_fills(basket.basket_id)
        for basket in before_restart.items
    }

    assert len(before_restart.items) == 2
    assert before_restart.realized_pnl == Decimal("2379.699")
    # Both rounds opened on the same candle, so what puts one first is the
    # one that closed later — the Spot round ran five minutes longer.
    spot, futures = before_restart.items
    assert futures.basket_id == futures_basket_id
    assert futures.status is BasketStatus.CLOSED
    assert futures.realized_pnl == Decimal("2356.0200")
    assert futures.leverage == 3
    assert spot.basket_id == spot_basket_id
    assert spot.status is BasketStatus.CLOSED
    assert spot.realized_pnl == Decimal("23.679")
    for fills in before_fills.values():
        assert [fill.side for fill in fills] == [FillSide.BUY, FillSide.SELL]
        assert all(fill.source is FillSource.PAPER_EXECUTOR for fill in fills)

    reopened_database = SQLiteDatabase(path)
    reopened_database.migrate()
    reopened = SQLiteTradeHistory(reopened_database)
    after_restart = reopened.list_basket_history(TradeHistoryFilter())
    after_fills = {
        basket.basket_id: reopened.list_fills(basket.basket_id)
        for basket in after_restart.items
    }
    assert after_restart == before_restart
    assert after_fills == before_fills

    window = _composed_window(path, monkeypatch)
    qtbot.addWidget(window)
    window.show()
    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    _show_every_day(window, qtbot)

    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 2)
    # The Spot round closed last, so it heads the list.
    assert _table_row_text(window.trade_history.basket_table, 0) == (
        "2026-01-01 09:05:00",
        "2026-01-01 09:45:00",
        "Spot",
        "BTC/USDT",
        "5m",
        "1",
        "249.94 USDT",
        "23.68 USDT",
    )
    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    _show_every_day(window, qtbot)
    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 4)
    assert set(_table_rows_text(window.trade_history.fill_table)) == {
        (
            "2026-01-01 09:05:00",
            "BTC/USDT",
            "Buy",
            "95.5",
            "314.136",
            "29,999.99 USDT",
            "—",
        ),
        (
            "2026-01-01 09:40:00",
            "BTC/USDT",
            "Sell",
            "103.0",
            "314.136",
            "32,356.01 USDT",
            "2,356.02 USDT",
        ),
        (
            "2026-01-01 09:05:00",
            "BTC/USDT",
            "Buy",
            "95",
            "2.631",
            "249.94 USDT",
            "—",
        ),
        (
            "2026-01-01 09:45:00",
            "BTC/USDT",
            "Sell",
            "104",
            "2.631",
            "273.62 USDT",
            "23.68 USDT",
        ),
    }


def _block_network(monkeypatch: MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("network must not run")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    monkeypatch.setattr(socket, "gethostbyname", fail_network)


def _composed_window(path: Path, monkeypatch: MonkeyPatch) -> MainWindow:
    assert QApplication.instance() is not None
    captured_window: MainWindow | None = None

    class NonBlockingApplication:
        @classmethod
        def instance(cls) -> "NonBlockingApplication":
            return cls()

        def exec(self) -> int:
            return 0

    class CapturedMainWindow(MainWindow):
        def __init__(self, **dependencies: object) -> None:
            nonlocal captured_window
            super().__init__(**dependencies)  # type: ignore[arg-type]
            captured_window = self

    monkeypatch.setattr(ui_desktop, "QApplication", NonBlockingApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", CapturedMainWindow)
    # This suite is about Trade History over a real composition, offline —
    # not the Setup screen's own chart preview, which reaches Binance's
    # public endpoint on its own as soon as the Setup screen shows.
    monkeypatch.setattr(desktop_main, "_default_chart_session", lambda: None)
    assert desktop_main.run_desktop(path) == 0
    assert captured_window is not None
    return captured_window


def _table_rows_text(table: QTableWidget) -> tuple[tuple[str, ...], ...]:
    return tuple(_table_row_text(table, row) for row in range(table.rowCount()))


def _table_row_text(table: QTableWidget, row: int) -> tuple[str, ...]:
    values: list[str] = []
    for column in range(table.columnCount()):
        values.append(table_item(table, row, column).text())
    return tuple(values)
