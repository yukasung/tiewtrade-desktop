import ast
import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

import tiewtrade.desktop_main as desktop_main
import tiewtrade.ui.desktop as ui_desktop
from tests.support.qt_interactions import click, qdate, table_item
from tests.support.trade_history_records import (
    basket_result,
    trade_fill,
)
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
)
from tiewtrade.application.trade_history import BasketHistoryResult, TradeHistoryFilter
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.trade_history import (
    SQLiteTradeHistory,
    TradeHistoryUnavailableError,
)
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
)
from tiewtrade.ui.main_window import MainWindow
from tiewtrade.ui.trade_history_page import FilterBar
from tiewtrade.ui.trading_workspace import WorkspaceTab

FUTURES_BASKET_ID = UUID("00000000-0000-0000-0000-000000000202")


def open_trade_history(window: MainWindow) -> None:
    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)


def show_every_day(bar: FilterBar, qtbot: QtBot) -> None:
    """Widen one half past the day it opens on, to reach a dated fixture.

    Each half carries its own range, so widening one says nothing about the
    other. The filter is held while a read is in flight, so this waits for it
    rather than clicking into nothing.
    """
    qtbot.waitUntil(bar.search_button.isEnabled)
    bar.from_date.clear_date()
    bar.to_date.clear_date()
    click(bar.search_button)


def test_desktop_trade_history_reads_durable_spot_and_futures_records(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    _record_spot_and_futures_history(history)
    basket_requests: list[TradeHistoryFilter] = []
    window = _composed_window(
        path,
        monkeypatch,
        basket_requests=basket_requests,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    # No session yet: the setup form is what the workspace offers.
    assert window.setup.isVisible()
    assert basket_requests == []

    open_trade_history(window)
    show_every_day(window.trade_history.basket_filters, qtbot)

    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 2)
    assert basket_requests[0].opened_from_utc is not None
    assert basket_requests[-1] == TradeHistoryFilter()
    assert _table_row_text(window.trade_history.basket_table, 0) == (
        "2026-01-03 07:00:00",
        "Futures",
        "BTC/USDT",
        "5m",
        "1",
        "200.00 USDT",
        "29.40 USDT",
    )
    assert _table_row_text(window.trade_history.basket_table, 1) == (
        "2026-01-01 07:00:00",
        "Spot",
        "BTC/USDT",
        "5m",
        "1",
        "200.00 USDT",
        "20.00 USDT",
    )
    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    # The Trades half opens on today too, and carries its own range.
    show_every_day(window.trade_history.trade_filters, qtbot)
    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 4)
    assert _table_rows_text(window.trade_history.fill_table) == (
        (
            "2026-01-03 08:00:00",
            "BTC/USDT",
            "Sell",
            "100",
            "2",
            "200.00 USDT",
            "29.40 USDT",
        ),
        (
            "2026-01-03 07:00:00",
            "BTC/USDT",
            "Buy",
            "100",
            "2",
            "200.00 USDT",
            "—",
        ),
        (
            "2026-01-02 07:00:00",
            "BTC/USDT",
            "Sell",
            "100",
            "2",
            "200.00 USDT",
            "20.00 USDT",
        ),
        (
            "2026-01-01 07:00:00",
            "BTC/USDT",
            "Buy",
            "100",
            "2",
            "200.00 USDT",
            "—",
        ),
    )


def test_desktop_trade_history_filters_all_rows_and_survives_restart(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    basket_requests: list[TradeHistoryFilter] = []
    for index in range(51):
        basket_id = UUID(int=index + 1)
        opened_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
        _record_closed(
            history,
            basket_result(
                basket_id=basket_id,
                opened_at_utc=opened_at,
                closed_at_utc=opened_at + timedelta(hours=1),
                timeframe="15m" if index == 50 else "5m",
                market_type=(MarketType.FUTURES if index == 50 else MarketType.SPOT),
                leverage=3 if index == 50 else None,
                realized_pnl=(Decimal("29.4") if index == 50 else Decimal("20")),
            ),
        )
    first = _composed_window(
        path,
        monkeypatch,
        basket_requests=basket_requests,
    )
    qtbot.addWidget(first)
    first.show()
    open_trade_history(first)
    show_every_day(first.trade_history.basket_filters, qtbot)
    qtbot.waitUntil(lambda: first.trade_history.basket_table.rowCount() == 51)
    assert table_item(first.trade_history.basket_table, 0, 3).text() == "15m"
    first_page_opened_at = tuple(
        table_item(first.trade_history.basket_table, row, 0).text() for row in range(51)
    )
    assert first_page_opened_at == tuple(sorted(first_page_opened_at, reverse=True))
    assert first_page_opened_at[0] == "2026-02-20 07:00:00"
    assert first_page_opened_at[-1] == "2026-01-01 07:00:00"

    # One Basket was opened each day, so a single day narrows 51 rows to one.
    newest_date = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=50)).date()
    first.trade_history.basket_filters.from_date.setDate(qdate(newest_date))
    first.trade_history.basket_filters.to_date.setDate(qdate(newest_date))
    click(first.trade_history.basket_filters.search_button)
    qtbot.waitUntil(lambda: first.trade_history.basket_table.rowCount() == 1)
    assert table_item(first.trade_history.basket_table, 0, 3).text() == "15m"
    assert table_item(first.trade_history.basket_table, 0, 1).text() == "Futures"
    assert basket_requests[-1] == TradeHistoryFilter(
        # The picked day is a Bangkok day, which opens at 17:00 UTC.
        opened_from_utc=datetime(2026, 2, 19, 17, 0, tzinfo=UTC),
        opened_before_utc=datetime(2026, 2, 20, 17, 0, tzinfo=UTC),
    )

    click(first.trade_history.basket_filters.reset_button)
    assert first.trade_history.basket_filters.from_date.has_date()
    assert first.trade_history.basket_filters.to_date.has_date()
    show_every_day(first.trade_history.basket_filters, qtbot)
    qtbot.waitUntil(lambda: first.trade_history.basket_table.rowCount() == 51)
    first.close()

    restarted = _composed_window(path, monkeypatch)
    qtbot.addWidget(restarted)
    restarted.show()
    open_trade_history(restarted)
    show_every_day(restarted.trade_history.basket_filters, qtbot)
    qtbot.waitUntil(lambda: restarted.trade_history.basket_table.rowCount() == 51)
    assert table_item(restarted.trade_history.basket_table, 0, 3).text() == "15m"


def test_trade_history_read_failure_is_fail_closed_and_sanitized(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    basket_calls = 0

    def succeed_then_fail(
        filters: TradeHistoryFilter,
    ) -> BasketHistoryResult:
        nonlocal basket_calls
        del filters
        basket_calls += 1
        if basket_calls == 1:
            return BasketHistoryResult(
                items=(basket,),
                realized_pnl=basket.realized_pnl,
            )
        raise TradeHistoryUnavailableError(
            "SQLite failed at /private/tmp/tiewtrade.sqlite3"
        )

    window = MainWindow(
        create_session=_unused_create,
        load_active=_no_active_session,
        list_basket_history=succeed_then_fail,
        list_trade_fills=lambda _filters: (),
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    show_every_day(window.trade_history.basket_filters, qtbot)
    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 1)

    click(window.trade_history.basket_filters.search_button)
    qtbot.waitUntil(
        lambda: (
            window.trade_history.basket_state.text()
            == "Stale · Position History unavailable"
        )
    )

    assert window.trade_history.basket_table.rowCount() == 1
    assert window.trade_history.fill_table.rowCount() == 0
    assert window.trade_history.retry_baskets_button.isVisible()
    assert "/private/tmp" not in window.trade_history.basket_state.text()


def test_trade_history_desktop_flow_has_no_forbidden_import_or_network(
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    forbidden = (
        "sqlite",
        "strategy",
        "strategies",
        "execution",
        "binance",
        "aiohttp",
    )
    ui_paths = tuple(Path("src/tiewtrade/ui").rglob("*.py"))
    assert ui_paths
    for path in ui_paths:
        tree = ast.parse(path.read_text())
        assert all(
            not any(name in module for name in forbidden)
            for module in _imported_names(tree)
        )

    composition_paths = (
        Path("src/tiewtrade/ui/desktop.py"),
        Path("src/tiewtrade/ui/main_window.py"),
    )
    network_dependencies = (
        "binance",
        "aiohttp",
        "httpx",
        "requests",
        "urllib",
        "websocket",
        "socket",
    )
    forbidden_source_references = (
        "tiewtrade.integrations.binance",
        "aiohttp.",
        "httpx.",
        "requests.",
        "urllib.",
        "websocket.",
        "websockets.",
        "socket.",
    )
    assert all(path.is_file() for path in composition_paths)
    for path in composition_paths:
        source = path.read_text()
        imported_names = _imported_names(ast.parse(source))
        assert all(
            not any(name in imported for name in network_dependencies)
            for imported in imported_names
        )
        source_lower = source.casefold()
        assert all(
            reference not in source_lower for reference in forbidden_source_references
        )

    def fail_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("network must not run")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    monkeypatch.setattr(socket, "gethostbyname", fail_network)
    window = _composed_window(tmp_path / "tiewtrade.sqlite3", monkeypatch)
    qtbot.addWidget(window)
    window.show()
    assert window.setup.isVisible()
    open_trade_history(window)
    show_every_day(window.trade_history.basket_filters, qtbot)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )
    assert window.workspace.tab_pages.currentWidget() is window.workspace.trade_history
    assert window.trade_history.basket_state.text() == "No Position history"


def _record_spot_and_futures_history(history: SQLiteTradeHistory) -> None:
    _record_closed(history, basket_result())
    futures_opened_at = datetime(2026, 1, 3, tzinfo=UTC)
    _record_closed(
        history,
        basket_result(
            basket_id=FUTURES_BASKET_ID,
            session_id=UUID("00000000-0000-0000-0000-000000000201"),
            market_type=MarketType.FUTURES,
            leverage=3,
            opened_at_utc=futures_opened_at,
            closed_at_utc=futures_opened_at + timedelta(hours=1),
            realized_pnl=Decimal("29.4"),
        ),
    )


def _record_closed(history: SQLiteTradeHistory, closed: BasketResult) -> None:
    assert closed.closed_at_utc is not None
    opened = replace(
        closed,
        closed_at_utc=None,
        realized_pnl=Decimal("0"),
        status=BasketStatus.OPEN,
    )
    buy = trade_fill(
        basket_id=opened.basket_id,
        session_id=opened.session_id,
        fill_id=f"{opened.basket_id}-buy",
        order_id=f"{opened.basket_id}-buy-order",
        filled_at_utc=opened.opened_at_utc,
    )
    sell = trade_fill(
        basket_id=closed.basket_id,
        session_id=closed.session_id,
        fill_id=f"{closed.basket_id}-sell",
        order_id=f"{closed.basket_id}-sell-order",
        side=FillSide.SELL,
        entry_number=None,
        filled_at_utc=closed.closed_at_utc,
        realized_pnl=closed.realized_pnl,
    )
    history.record_open_basket(opened, buy)
    history.record_closed_basket(closed, sell)


def _table_rows_text(table: QTableWidget) -> tuple[tuple[str, ...], ...]:
    return tuple(_table_row_text(table, row) for row in range(table.rowCount()))


def _table_row_text(table: QTableWidget, row: int) -> tuple[str, ...]:
    values: list[str] = []
    for column in range(table.columnCount()):
        values.append(table_item(table, row, column).text())
    return tuple(values)


def _imported_names(tree: ast.AST) -> tuple[str, ...]:
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name.casefold())
                if alias.asname is not None:
                    imported_names.append(alias.asname.casefold())
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").casefold()
            for alias in node.names:
                imported_names.append(f"{module}.{alias.name.casefold()}")
                if alias.asname is not None:
                    imported_names.append(alias.asname.casefold())
    return tuple(imported_names)


def _composed_window(
    path: Path,
    monkeypatch: MonkeyPatch,
    *,
    basket_requests: list[TradeHistoryFilter] | None = None,
) -> MainWindow:
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

    if basket_requests is not None:
        original_list_baskets = SQLiteTradeHistory.list_basket_history

        def capture_basket_request(
            history: SQLiteTradeHistory,
            filters: TradeHistoryFilter,
        ) -> BasketHistoryResult:
            basket_requests.append(filters)
            return original_list_baskets(history, filters)

        monkeypatch.setattr(
            SQLiteTradeHistory,
            "list_basket_history",
            capture_basket_request,
        )

    monkeypatch.setattr(ui_desktop, "QApplication", NonBlockingApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", CapturedMainWindow)
    assert desktop_main.run_desktop(path) == 0
    assert captured_window is not None
    return captured_window


def _no_active_session() -> ConfiguredPaperSession | None:
    return None


def _unused_create(
    values: PaperSessionSetupValues,
) -> PaperSessionCreateOutcome:
    del values
    pytest.fail("create must not run")


def test_opening_the_trade_half_first_shows_trades_not_baskets(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """The half the trader opened is the half that is read and shown."""
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    _record_closed(history, basket_result())

    window = _composed_window(path, monkeypatch)
    qtbot.addWidget(window)
    window.show()

    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    show_every_day(window.trade_history.trade_filters, qtbot)

    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() > 0)
    assert window.trade_history.fill_table.isVisible()
    assert not window.trade_history.basket_table.isVisible()
    assert window.workspace.current_tab is WorkspaceTab.TRADE_HISTORY
