import ast
import inspect
import threading
from collections.abc import Callable
from copy import copy
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import overload
from uuid import UUID

import pytest
from PySide6.QtCore import QDeadlineTimer, QPoint, QRect, Qt, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

import tiewtrade.ui.main_window as main_window_module
from tests.support.chart_candles import flat_candle
from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tests.support.qt_interactions import click, qdate
from tests.support.trade_history_records import basket_result, trade_fill
from tests.support.trade_history_ui import (
    empty_basket_history,
    empty_trade_fills,
)
from tests.support.workspace_snapshots import basket_entries
from tiewtrade.application.bot_control import (
    BotControlAction,
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.open_basket import (
    OpenBasketEntry,
    OpenBasketState,
    ReopenedBasket,
    reopen_basket,
)
from tiewtrade.application.paper_runtime import PaperPositionRestoreError
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
    PaperSessionUnavailableError,
    PaperSessionValidationError,
)
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.application.trading_workspace import (
    POSITION_BASKET_UNREADABLE_MESSAGE,
    BasketEntryGate,
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    paper_runtime_workspace_snapshot,
)
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.chart_workflow import ChartFactRelay
from tiewtrade.ui.display_time import display_today
from tiewtrade.ui.main_window import BotActions, ChartSources, MainWindow
from tiewtrade.ui.pair_text import pair_text
from tiewtrade.ui.session_overview import SessionOverviewWidget
from tiewtrade.ui.trade_history_presenter import (
    default_filter_values,
    trade_history_filter,
)
from tiewtrade.ui.trading_workspace import WorkspaceTab


def _runtime_state(window: MainWindow) -> BotRuntimeState | None:
    """The state the lifecycle reached, or None before a session exists."""
    snapshot = window._lifecycle_workflow._snapshot
    return None if snapshot is None else snapshot.state


def no_active_session() -> ConfiguredPaperSession | None:
    return None


def test_session_ready_loads_chart_for_configured_session(qtbot: QtBot) -> None:
    session = configured_spot_session()
    requests: list[tuple[ConfiguredPaperSession, ChartRange]] = []

    async def load_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        requests.append((configured, chart_range))
        return ChartSnapshot(
            session=configured,
            chart_range=chart_range,
            observed_at_utc=chart_range.end,
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            load=load_chart, clock=lambda: datetime(2026, 8, 2, 10, 3, tzinfo=UTC)
        ),
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: len(requests) == 1)

    configured, chart_range = requests[0]
    assert configured is session
    assert chart_range.end == datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
    assert chart_range.end - chart_range.start == session.market_data.interval * 120


def test_chart_view_selection_reloads_chart_without_bot_lifecycle(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    requests: list[tuple[str, str]] = []
    feed_selections: list[ChartViewSelection] = []

    async def load_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        requests.append(
            (configured.market_data.symbol, configured.market_data.timeframe)
        )
        return ChartSnapshot(
            session=configured,
            chart_range=chart_range,
            observed_at_utc=chart_range.end,
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            load=load_chart,
            select_view=lambda _session, selection: feed_selections.append(selection),
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: requests == [("BTCUSDT", "5m")])

    window.workspace.chart.symbol.setCurrentIndex(
        window.workspace.chart.symbol.findData("ETHUSDT")
    )

    qtbot.waitUntil(lambda: requests[-1] == ("ETHUSDT", "5m"))
    assert feed_selections == [ChartViewSelection(symbol="ETHUSDT", timeframe="5m")]


def test_chart_unavailable_does_not_disable_bot_control_or_trade_history(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()

    attempts = 0

    async def unavailable_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("public transport unavailable")
        return ChartSnapshot(
            session=configured,
            chart_range=chart_range,
            observed_at_utc=chart_range.end,
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(load=unavailable_chart),
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(
        lambda: (
            window.workspace.chart._snapshot is not None
            and window.workspace.chart._snapshot.state is ChartReadState.UNAVAILABLE
        )
    )

    assert window.setup.isVisible()
    assert window.workspace.bot_control.isEnabled()
    assert window.trade_history.isEnabled()
    assert window.workspace.chart.retry_button.isVisible()
    open_trade_history(window)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )
    click(window.workspace.chart.retry_button)
    qtbot.waitUntil(
        lambda: (
            window.workspace.chart._snapshot is not None
            and window.workspace.chart._snapshot.state is ChartReadState.EMPTY
        )
    )
    assert attempts == 2


def unused_create(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
    pytest.fail("create must not run")


def open_trade_history(window: MainWindow) -> None:
    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)


def _widget_rect_in_viewport(widget: QWidget, viewport: QWidget) -> QRect:
    return QRect(widget.mapTo(viewport, QPoint()), widget.size())


def _session_configuration_controls(window: MainWindow) -> tuple[QWidget, ...]:
    setup = window.setup
    return (
        setup.market_type,
        setup.symbol,
        setup.timeframe,
        setup.strategy,
        setup.capital_per_entry,
        setup.max_entries,
        setup.leverage,
        setup.create_button,
    )


def test_trade_history_opens_without_active_session(qtbot: QtBot) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    open_trade_history(window)

    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )
    assert window.trade_history.isVisible()
    # No session yet: the setup form is what the workspace offers.
    assert window.setup.isVisible()


def test_trade_history_remains_available_when_session_load_fails(
    qtbot: QtBot,
) -> None:
    def fail_load() -> ConfiguredPaperSession | None:
        raise PaperSessionUnavailableError(
            "SQLite failed at /private/tmp/session.sqlite3"
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=fail_load,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.unavailable_panel.isVisible)

    open_trade_history(window)

    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )


def test_tab_activation_starts_history_query_only_once(qtbot: QtBot) -> None:
    calls = 0

    def count_baskets(filters: TradeHistoryFilter) -> BasketHistoryResult:
        nonlocal calls
        calls += 1
        return empty_basket_history(filters)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=count_baskets,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    open_trade_history(window)
    qtbot.waitUntil(lambda: calls == 1)
    window.workspace.show_tab(WorkspaceTab.POSITION_BASKET)
    open_trade_history(window)

    assert calls == 1


def test_session_load_finishing_does_not_navigate_away_from_history(
    qtbot: QtBot,
) -> None:
    started = threading.Event()
    release = threading.Event()
    existing = configured_spot_session()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(2)

    def delayed_load() -> ConfiguredPaperSession | None:
        started.set()
        if not release.wait(timeout=1):
            raise TimeoutError("test did not release worker")
        return existing

    window = MainWindow(
        create_session=unused_create,
        load_active=delayed_load,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(started.is_set)

    open_trade_history(window)
    release.set()

    qtbot.waitUntil(
        lambda: (
            window.overview.symbol_value.text()
            == pair_text(existing.market_data.symbol)
        )
    )
    assert window.trade_history.isVisible()
    assert window.overview.isVisible() is False
    assert _runtime_state(window) is BotRuntimeState.CONFIGURED


def test_basket_and_trade_retries_are_wired_to_their_own_tab(
    qtbot: QtBot,
) -> None:
    """Each tab retries its own query; neither button reaches the other's."""
    basket = basket_result()
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )
    basket_calls = 0
    trade_calls = 0

    def fail_baskets_once(filters: TradeHistoryFilter) -> BasketHistoryResult:
        nonlocal basket_calls
        del filters
        basket_calls += 1
        if basket_calls == 1:
            raise RuntimeError("private basket failure")
        return BasketHistoryResult(
            items=(basket,),
            realized_pnl=Decimal("0"),
        )

    def fail_trades_once(
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        nonlocal trade_calls
        del filters
        trade_calls += 1
        if trade_calls == 1:
            raise RuntimeError("private fill failure")
        return (item,)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=fail_baskets_once,
        list_trade_fills=fail_trades_once,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    unavailable = "Position History unavailable"
    qtbot.waitUntil(lambda: window.trade_history.basket_state.text() == unavailable)

    click(window.trade_history.retry_baskets_button)
    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 1)

    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    qtbot.waitUntil(
        lambda: window.trade_history.fill_state.text() == "Trade History unavailable"
    )
    click(window.trade_history.retry_fills_button)

    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 1)
    assert basket_calls == 2
    assert trade_calls == 2


def test_trade_history_filter_and_reset_requests_are_wired(qtbot: QtBot) -> None:
    basket = basket_result()
    basket_calls: list[TradeHistoryFilter] = []

    def list_basket_history(filters: TradeHistoryFilter) -> BasketHistoryResult:
        basket_calls.append(filters)
        return BasketHistoryResult(items=(basket,), realized_pnl=Decimal("0"))

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=list_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 1)

    window.trade_history.basket_filters.from_date.setDate(qdate(date(2026, 1, 2)))
    window.trade_history.basket_filters.to_date.clear_date()
    click(window.trade_history.basket_filters.search_button)

    qtbot.waitUntil(lambda: len(basket_calls) == 2)
    assert basket_calls[-1] == TradeHistoryFilter(
        opened_from_utc=datetime(2026, 1, 1, 17, tzinfo=UTC)
    )

    click(window.trade_history.basket_filters.reset_button)

    qtbot.waitUntil(lambda: len(basket_calls) == 3)
    # Reset asks for the day the page opens on, not for everything.
    assert basket_calls[-1] == trade_history_filter(
        default_filter_values(display_today())
    )


def test_trade_history_filter_validation_is_wired_without_starting_query(
    qtbot: QtBot,
) -> None:
    basket_calls = 0

    def count_baskets(filters: TradeHistoryFilter) -> BasketHistoryResult:
        nonlocal basket_calls
        basket_calls += 1
        return empty_basket_history(filters)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=count_baskets,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(lambda: basket_calls == 1)
    qtbot.waitUntil(window.trade_history.basket_filters.search_button.isEnabled)
    window.trade_history.basket_filters.from_date.setDate(qdate(date(2026, 1, 2)))
    window.trade_history.basket_filters.to_date.setDate(qdate(date(2026, 1, 1)))

    click(window.trade_history.basket_filters.search_button)

    qtbot.waitUntil(window.trade_history.basket_filters.filter_error.isVisible)
    assert (
        window.trade_history.basket_filters.filter_error.text()
        == "From Date must not be after To Date"
    )
    assert basket_calls == 1


def test_main_window_delegates_session_task_lifecycle_to_workflow() -> None:
    source = inspect.getsource(main_window_module)
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "SessionWorkflow" in imported_names
    assert "SessionTask" not in imported_names
    assert "_active_create_task" not in source
    assert "_active_load_task" not in source
    assert "_callback_generation" not in source


def test_created_spot_session_returns_to_editable_form_without_runtime_actions(
    qtbot: QtBot,
) -> None:
    created = configured_spot_session()
    window = MainWindow(
        create_session=lambda values: PaperSessionCreateOutcome(created, True),
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    # What the fixture Session spends per Entry, so the form coming back
    # showing that Session reads as the form the trader filled in.
    window.setup.capital_per_entry.setText("20000")

    click(window.setup.create_button)

    qtbot.waitUntil(lambda: window._replacement_session == created)
    assert window.setup.market_type.currentData() == "spot"
    assert window.setup.timeframe.currentData() == "5m"
    assert window.setup.capital_per_entry.text() == "20000"


def test_futures_overview_shows_immutable_policy(qtbot: QtBot) -> None:
    session = configured_futures_session(leverage=3)
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(session)

    assert overview.market_value.text() == "Futures"
    assert overview.margin_mode_value.text() == "Cross Margin"
    assert overview.position_mode_value.text() == "One-way Mode"


def test_futures_overview_marks_missing_required_policy_unavailable(
    qtbot: QtBot,
) -> None:
    session = _session_with_policy_changes(
        configured_futures_session(), futures_policy=None
    )
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(session)

    assert overview.margin_mode_value.text() == "Unavailable"
    assert overview.position_mode_value.text() == "Unavailable"


def test_spot_overview_marks_a_mixed_futures_policy_unavailable(
    qtbot: QtBot,
) -> None:
    """A Spot Session carries no policy, so holding one is malformed."""
    session = _session_with_policy_changes(
        configured_spot_session(),
        futures_policy=FuturesTradingPolicy.v1(leverage=3),
    )
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(session)

    assert overview.margin_mode_value.text() == "Unavailable"
    assert overview.position_mode_value.text() == "Unavailable"


def test_spot_overview_leaves_the_futures_rows_blank(qtbot: QtBot) -> None:
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(configured_spot_session())

    assert overview.margin_mode_value.text() == "—"
    assert overview.position_mode_value.text() == "—"
    assert not hasattr(overview, "spot_ratio_value")
    assert not hasattr(overview, "spot_reserve_ratio_value")


def test_repeated_submit_while_worker_is_running_calls_create_once(
    qtbot: QtBot,
) -> None:
    started = threading.Event()
    release = threading.Event()
    created = configured_spot_session()
    calls: list[PaperSessionSetupValues] = []

    def create(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        calls.append(values)
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test did not release worker")
        return PaperSessionCreateOutcome(created, True)

    window = MainWindow(
        create_session=create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("200000")

    click(window.setup.create_button)
    qtbot.waitUntil(started.is_set)
    click(window.setup.create_button)

    assert len(calls) == 1
    release.set()
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(
        lambda: all(
            control.isEnabled() for control in _session_configuration_controls(window)
        )
    )


def test_validation_failure_restores_form_and_shows_field_error(qtbot: QtBot) -> None:
    def reject(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        raise PaperSessionValidationError(
            "capital_per_entry", "Amount must be positive"
        )

    window = MainWindow(
        create_session=reject,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("0")

    click(window.setup.create_button)

    qtbot.waitUntil(
        lambda: window.setup.capital_per_entry_error.text() == "Amount must be positive"
    )
    assert window.setup.isVisible()
    assert window.setup.create_button.isEnabled() is True
    assert window.setup.capital_per_entry.text() == "0"


def test_an_out_of_range_value_reveals_and_focuses_its_field(
    qtbot: QtBot,
) -> None:
    def unused_create_active(
        _session: ConfiguredPaperSession,
    ) -> PaperSessionCreateOutcome:
        pytest.fail("invalid setup must not create a session")

    create_session = CreatePaperSession(create_active=unused_create_active)
    window = MainWindow(
        create_session=create_session.execute,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    window.setup.capital_per_entry.setText("0")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)

    qtbot.waitUntil(
        lambda: window.setup.capital_per_entry_error.text() == "Amount must be positive"
    )
    viewport = window.workspace.bot_control_scroll.viewport()

    assert window.workspace.bot_control.isVisible()
    assert window.setup.capital_per_entry_error.isVisible()
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.capital_per_entry, viewport)
    )
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.capital_per_entry_error, viewport)
    )
    assert window.setup.capital_per_entry.hasFocus()


def test_a_required_field_left_empty_is_revealed_and_focused(
    qtbot: QtBot,
) -> None:
    """A rejected field is scrolled to and focused, not just marked in red."""

    def unused_create_active(
        _session: ConfiguredPaperSession,
    ) -> PaperSessionCreateOutcome:
        pytest.fail("invalid setup must not create a session")

    create_session = CreatePaperSession(create_active=unused_create_active)
    window = MainWindow(
        create_session=create_session.execute,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    window.setup.capital_per_entry.clear()
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)

    control = window.setup.capital_per_entry
    error_label = window.setup.capital_per_entry_error
    qtbot.waitUntil(lambda: error_label.text() == "This field is required")
    viewport = window.workspace.bot_control_scroll.viewport()

    assert error_label.isVisible()
    assert viewport.rect().contains(_widget_rect_in_viewport(control, viewport))
    assert viewport.rect().contains(_widget_rect_in_viewport(error_label, viewport))
    assert control.hasFocus()


def test_delayed_validation_reveals_the_failed_field(
    qtbot: QtBot,
) -> None:
    started = threading.Event()
    release = threading.Event()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)

    def unused_create_active(
        _session: ConfiguredPaperSession,
    ) -> PaperSessionCreateOutcome:
        pytest.fail("invalid setup must not create a session")

    create_session = CreatePaperSession(create_active=unused_create_active)

    def delayed_create(
        values: PaperSessionSetupValues,
    ) -> PaperSessionCreateOutcome:
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test did not release worker")
        return create_session.execute(values)

    window = MainWindow(
        create_session=delayed_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("0")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)
    qtbot.waitUntil(started.is_set)

    release.set()

    qtbot.waitUntil(
        lambda: window.setup.capital_per_entry_error.text() == "Amount must be positive"
    )
    viewport = window.workspace.bot_control_scroll.viewport()

    assert window.workspace.bot_control.isVisible()
    assert window.setup.capital_per_entry_error.isVisible()
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.capital_per_entry, viewport)
    )
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.capital_per_entry_error, viewport)
    )
    assert window.setup.capital_per_entry.hasFocus()
    assert thread_pool.waitForDone(1_000)


def test_delayed_spot_validation_freezes_configuration_until_result(
    qtbot: QtBot,
) -> None:
    started = threading.Event()
    release = threading.Event()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)

    def unused_create_active(
        _session: ConfiguredPaperSession,
    ) -> PaperSessionCreateOutcome:
        pytest.fail("invalid setup must not create a session")

    create_session = CreatePaperSession(create_active=unused_create_active)

    def delayed_create(
        values: PaperSessionSetupValues,
    ) -> PaperSessionCreateOutcome:
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test did not release worker")
        return create_session.execute(values)

    window = MainWindow(
        create_session=delayed_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("0")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)
    qtbot.waitUntil(started.is_set)

    controls = _session_configuration_controls(window)
    assert all(not control.isEnabled() for control in controls)
    QTest.keyClick(window.setup.market_type, Qt.Key.Key_Down)
    assert window.setup.market_type.currentData() == "spot"
    release.set()

    qtbot.waitUntil(
        lambda: window.setup.capital_per_entry_error.text() == "Amount must be positive"
    )
    qtbot.waitUntil(lambda: all(control.isEnabled() for control in controls))
    viewport = window.workspace.bot_control_scroll.viewport()
    assert window.setup.market_type.currentData() == "spot"
    assert window.setup.capital_per_entry.text() == "0"
    assert window.setup.capital_per_entry_error.isVisible()
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.capital_per_entry, viewport)
    )
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.capital_per_entry_error, viewport)
    )
    assert window.setup.capital_per_entry.hasFocus()
    assert thread_pool.waitForDone(1_000)


def test_persistence_failure_shows_sanitized_unavailable_state_and_allows_retry(
    qtbot: QtBot,
) -> None:
    def unavailable(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        raise PaperSessionUnavailableError(
            "sqlite3.OperationalError: unable to open /private/tmp/tiewtrade.sqlite3"
        )

    window = MainWindow(
        create_session=unavailable,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    click(window.setup.create_button)

    qtbot.waitUntil(window.unavailable_panel.isVisible)
    assert window.unavailable_message.text() == "Session storage is unavailable"
    assert "/private/tmp" not in window.unavailable_message.text()
    qtbot.waitUntil(window.unavailable_retry_button.isEnabled)
    assert all(
        control.isEnabled() for control in _session_configuration_controls(window)
    )
    click(window.unavailable_retry_button)
    qtbot.waitUntil(window.setup.isVisible)
    assert window.setup.create_button.isEnabled() is True


def test_unknown_create_failure_shows_sanitized_unavailable_state(qtbot: QtBot) -> None:
    def fail_create(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        raise RuntimeError("sqlite error at /private/tmp/tiewtrade.sqlite3")

    window = MainWindow(
        create_session=fail_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    click(window.setup.create_button)

    qtbot.waitUntil(window.unavailable_panel.isVisible)
    assert window.unavailable_message.text() == "Paper Session could not be created"
    assert "/private/tmp" not in window.unavailable_message.text()


def test_existing_create_outcome_opens_editable_form(qtbot: QtBot) -> None:
    existing = configured_spot_session()
    window = MainWindow(
        create_session=lambda values: PaperSessionCreateOutcome(existing, False),
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("200000")

    click(window.setup.create_button)

    qtbot.waitUntil(window.setup.isVisible)
    assert window.setup.timeframe.currentData() == existing.market_data.timeframe


def test_existing_active_session_opens_editable_form_without_create(
    qtbot: QtBot,
) -> None:
    existing = configured_spot_session()
    window = MainWindow(
        create_session=lambda values: pytest.fail("must not create"),
        load_active=lambda: existing,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(window.setup.isVisible)

    assert window.setup.timeframe.currentData() == existing.market_data.timeframe


def test_sqlite_failure_shows_unavailable_without_fake_overview(qtbot: QtBot) -> None:
    def fail_load() -> ConfiguredPaperSession | None:
        raise PaperSessionUnavailableError(
            "Active Paper Session read failed at /private/tmp/tiewtrade.sqlite3"
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=fail_load,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(window.unavailable_panel.isVisible)

    assert window.unavailable_message.text() == "Session storage is unavailable"
    assert "/private/tmp" not in window.unavailable_message.text()
    assert not window.overview.isVisible()


def test_corrupt_sqlite_session_stays_unavailable_without_persisted_text(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    store.create(configured_spot_session())
    unsupported_symbol = "PRIVATE-PERSISTED-SYMBOL"
    with database.connect() as connection:
        connection.execute(
            "UPDATE bot_sessions SET symbol = ?",
            (unsupported_symbol,),
        )
    window = MainWindow(
        create_session=unused_create,
        load_active=store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(window.unavailable_panel.isVisible)

    assert window.unavailable_message.text() == "Session storage is unavailable"
    assert unsupported_symbol not in window.unavailable_message.text()
    assert not window.overview.isVisible()


def test_retry_after_startup_load_failure_reloads_existing_session(
    qtbot: QtBot,
) -> None:
    existing = configured_spot_session()
    load_calls = 0

    def fail_then_load_existing() -> ConfiguredPaperSession | None:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            raise PaperSessionUnavailableError(
                "sqlite3.OperationalError: unable to open "
                "/private/tmp/tiewtrade.sqlite3"
            )
        return existing

    window = MainWindow(
        create_session=unused_create,
        load_active=fail_then_load_existing,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(window.unavailable_panel.isVisible)
    qtbot.waitUntil(window.unavailable_retry_button.isEnabled)
    assert load_calls == 1
    assert not window.setup.isVisible()

    click(window.unavailable_retry_button)

    qtbot.waitUntil(window.setup.isVisible)
    assert load_calls == 2
    assert window.setup.timeframe.currentData() == existing.market_data.timeframe


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        pytest.param(
            PaperSessionValidationError("capital_per_entry", "invalid persisted value"),
            "Paper Session could not be loaded",
            id="validation",
        ),
        pytest.param(
            PaperSessionUnavailableError(
                "SQLite failed at /private/tmp/tiewtrade.sqlite3"
            ),
            "Session storage is unavailable",
            id="storage",
        ),
        pytest.param(
            RuntimeError("unexpected SQLite failure at /private/tmp/tiewtrade.sqlite3"),
            "Paper Session could not be loaded",
            id="unknown",
        ),
    ],
)
def test_load_failures_stay_fail_closed_with_sanitized_copy(
    qtbot: QtBot,
    error: Exception,
    expected_message: str,
) -> None:
    def fail_load() -> ConfiguredPaperSession | None:
        raise error

    window = MainWindow(
        create_session=unused_create,
        load_active=fail_load,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(window.unavailable_panel.isVisible)

    assert window.unavailable_message.text() == expected_message
    assert not window.setup.isVisible()
    assert window.setup.capital_per_entry_error.text() == ""
    assert "private/tmp" not in window.unavailable_message.text()


def test_closing_window_ignores_late_worker_result_and_releases_task(
    qtbot: QtBot,
) -> None:
    started = threading.Event()
    release = threading.Event()
    existing = configured_spot_session()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)

    def delayed_load() -> ConfiguredPaperSession | None:
        started.set()
        release.wait(timeout=1)
        return existing

    window = MainWindow(
        create_session=unused_create,
        load_active=delayed_load,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(started.is_set)
    overview_before_close = window.overview.symbol_value.text()
    unavailable_before_close = window.unavailable_message.text()

    window.close()
    release.set()
    assert thread_pool.waitForDone(1_000)
    qtbot.wait(20)

    assert not window.isVisible()
    assert window.overview.symbol_value.text() == overview_before_close
    assert window.unavailable_message.text() == unavailable_before_close
    assert not window.overview.isVisible()


def test_closing_window_waits_for_workers_after_closing_workflows(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    caller_thread = threading.get_ident()
    wait_threads: list[int] = []

    class RecordingThreadPool(QThreadPool):
        @overload
        def waitForDone(self, msecs: int, /) -> bool: ...

        @overload
        def waitForDone(
            self,
            /,
            deadline: QDeadlineTimer | QDeadlineTimer.ForeverConstant | int = -1,
        ) -> bool: ...

        def waitForDone(
            self,
            deadline: QDeadlineTimer | QDeadlineTimer.ForeverConstant | int = -1,
        ) -> bool:
            wait_threads.append(threading.get_ident())
            events.append(f"pool:{deadline}")
            return super().waitForDone(deadline)

    thread_pool = RecordingThreadPool()
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    session_close = window._workflow.close
    history_close = window._history_workflow.close

    def close_session_workflow() -> None:
        events.append("session")
        session_close()

    def close_history_workflow() -> None:
        events.append("history")
        history_close()

    monkeypatch.setattr(window._workflow, "close", close_session_workflow)
    monkeypatch.setattr(window._history_workflow, "close", close_history_workflow)

    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())

    assert events == ["session", "history", "pool:5000"]
    assert len(wait_threads) == 1
    assert wait_threads[0] != caller_thread


def test_closing_window_invokes_controlled_runtime_shutdown(qtbot: QtBot) -> None:
    caller_thread = threading.get_ident()
    shutdown_started = threading.Event()
    release_shutdown = threading.Event()
    shutdown_threads: list[int] = []

    def shutdown_runtime() -> None:
        shutdown_threads.append(threading.get_ident())
        shutdown_started.set()
        if not release_shutdown.wait(timeout=2):
            raise TimeoutError("test did not release shutdown")

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(shutdown_runtime=shutdown_runtime),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    window.close()
    qtbot.waitUntil(shutdown_started.is_set)

    assert window.isVisible()
    assert len(shutdown_threads) == 1
    assert shutdown_threads[0] != caller_thread

    release_shutdown.set()
    qtbot.waitUntil(lambda: not window.isVisible())

    assert len(shutdown_threads) == 1


def test_closing_window_does_not_stop_chart_feed_on_qt_thread(qtbot: QtBot) -> None:
    stopped: list[bool] = []
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(stop_feed=lambda: stopped.append(True)),
    )
    qtbot.addWidget(window)

    window._close_workflows()

    assert stopped == []


def test_closing_window_ignores_late_trade_history_result(qtbot: QtBot) -> None:
    started = threading.Event()
    release = threading.Event()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)

    def delayed_baskets(filters: TradeHistoryFilter) -> BasketHistoryResult:
        started.set()
        if not release.wait(timeout=1):
            raise TimeoutError("test did not release worker")
        return empty_basket_history(filters)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=delayed_baskets,
        list_trade_fills=empty_trade_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    open_trade_history(window)
    qtbot.waitUntil(started.is_set)
    basket_state_before_close = window.trade_history.basket_state.text()

    window.close()
    release.set()
    assert thread_pool.waitForDone(1_000)
    qtbot.wait(20)

    assert not window.isVisible()
    assert window.trade_history.basket_state.text() == basket_state_before_close


def test_main_window_composes_workspace_and_starts_on_setup(
    qtbot: QtBot,
) -> None:
    def operation(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        return PaperSessionCreateOutcome(configured_spot_session(), True)

    window = MainWindow(
        create_session=operation,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    assert window.centralWidget() is window.workspace
    assert window.setup is window.workspace.setup
    assert window.overview is window.workspace.overview
    assert window.trade_history is window.workspace.trade_history


def test_lifecycle_actions_render_matching_header_and_control_snapshot(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    start_calls = 0
    stop_calls = 0

    def start_bot(snapshot: object) -> BotLifecycleResult:
        nonlocal start_calls
        assert isinstance(snapshot, BotControlSnapshot)
        start_calls += 1
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def stop_bot(snapshot: object) -> BotLifecycleResult:
        nonlocal stop_calls
        assert isinstance(snapshot, BotControlSnapshot)
        stop_calls += 1
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.STOPPED,
            )
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start_bot, stop=stop_bot),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.workspace.bot_control_widget.start_button.isEnabled()
    )

    click(window.workspace.bot_control_widget.start_button)
    click(window.workspace.bot_control_widget.start_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert start_calls == 1
    assert window.workspace.bot_control_widget.stop_button.isVisible()
    assert window.findChildren(QWidget, "manualBuyButton") == []
    assert window.findChildren(QWidget, "manualSellButton") == []

    click(window.workspace.bot_control_widget.stop_button)
    confirmation = window.findChild(QMessageBox)
    assert confirmation is not None
    confirm_button = next(
        button for button in confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(confirm_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.STOPPED)
    assert stop_calls == 1


def test_blocked_runtime_reopens_the_editable_setup_form(
    qtbot: QtBot,
) -> None:
    def fail_start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        raise RuntimeError("raw fake exception must not reach the UI")

    window = MainWindow(
        create_session=unused_create,
        load_active=configured_spot_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=fail_start),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.workspace.bot_control_widget.start_button.isEnabled()
    )

    click(window.workspace.bot_control_widget.start_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.BLOCKED)
    qtbot.waitUntil(window.setup.isVisible)
    assert window.setup.capital_per_entry.isEnabled()


def test_stop_reopens_an_editable_setup_form(qtbot: QtBot) -> None:
    active: ConfiguredPaperSession | None = configured_spot_session()

    def load_active() -> ConfiguredPaperSession | None:
        return active

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def stop(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace, BotRuntimeState.STOPPED
            )
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=load_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start, stop=stop),
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    click(control.stop_button)
    stop_confirmation = window.findChild(QMessageBox)
    assert stop_confirmation is not None
    stop_button = next(
        button for button in stop_confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(stop_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.STOPPED)

    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.timeframe.isEnabled)
    assert window.setup.timeframe.currentData() == "5m"
    assert window.setup.capital_per_entry.isEnabled()


def test_start_from_setup_creates_and_starts_paper_bot_in_one_action(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    created: list[PaperSessionSetupValues] = []
    started: list[BotControlSnapshot] = []

    def create(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        created.append(values)
        return PaperSessionCreateOutcome(session=session, created=True)

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        started.append(snapshot)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    window = MainWindow(
        create_session=create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("1000")

    click(window.setup.create_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(created) == 1
    assert created[0].capital_per_entry == "1000"
    assert len(started) == 1


def test_start_from_setup_skips_recovery_inspection_for_new_session(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    initialize_calls: list[BotControlSnapshot] = []
    start_calls: list[BotControlSnapshot] = []

    def create(_values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        return PaperSessionCreateOutcome(session=session, created=True)

    def initialize(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        initialize_calls.append(snapshot)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace, BotRuntimeState.BLOCKED
            ),
            blocked_reason="Paper Bot recovery failed",
        )

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        start_calls.append(snapshot)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    window = MainWindow(
        create_session=create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start, initialize=initialize),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.capital_per_entry.setText("1000")

    click(window.setup.create_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert initialize_calls == []
    assert len(start_calls) == 1


def test_stop_without_exposure_keeps_session_and_reopens_prefilled_form(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    retired: list[ConfiguredPaperSession] = []

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def stop(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace, BotRuntimeState.STOPPED
            )
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start, stop=stop),
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    click(control.stop_button)
    confirmation = window.findChild(QMessageBox)
    assert confirmation is not None
    stop_button = next(
        button for button in confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(stop_button)

    qtbot.waitUntil(window.setup.isVisible)
    assert retired == []
    assert window.setup.capital_per_entry.text() == format(
        session.config.capital_per_entry, "f"
    )
    assert window.setup.timeframe.currentData() == "5m"


def test_stop_with_open_position_reopens_editable_setup_without_closing(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    created: list[PaperSessionSetupValues] = []
    events: list[str] = []
    runtime_snapshots = RuntimeSnapshotRelay()
    observed_at = datetime.now(UTC)
    basket = BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=1,
        entries=basket_entries("100", quantity="1", current_price="110"),
        total_quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        current_price=Decimal("110"),
        take_profit_price=Decimal("120"),
        unrealized_pnl=Decimal("10"),
        liquidation_price=None,
        entry_gate=BasketEntryGate.OPEN,
        cooldown_until=None,
        updated_at_utc=observed_at,
    )

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def stop(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace, BotRuntimeState.STOPPED
            )
        )

    def create(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        created.append(values)
        return PaperSessionCreateOutcome(session=session, created=True)

    def close_position(_session: ConfiguredPaperSession) -> None:
        events.append("close")

    def retire_session(_session: ConfiguredPaperSession) -> None:
        events.append("retire")

    window = MainWindow(
        create_session=create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(
            start=start,
            stop=stop,
            close_position=close_position,
            has_open_position=lambda _session: True,
            retire_session=retire_session,
            runtime_snapshots=runtime_snapshots,
        ),
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    runtime_snapshots.new_generation()(
        BotLifecycleResult(
            workspace=paper_runtime_workspace_snapshot(
                window._lifecycle_workflow.snapshot.workspace,
                basket=basket,
                observed_at_utc=observed_at,
            )
        )
    )
    qtbot.waitUntil(
        lambda: window._lifecycle_workflow.snapshot.workspace.basket == basket
    )
    click(control.stop_button)
    stop_dialog = window.findChild(QMessageBox)
    assert stop_dialog is not None
    click(
        next(button for button in stop_dialog.buttons() if button.text() == "Stop Bot")
    )
    qtbot.waitUntil(window.setup.isVisible)
    assert window._lifecycle_workflow.snapshot.workspace.basket == basket
    assert events == []

    def confirm_close() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        click(
            next(
                button
                for button in dialog.buttons()
                if button.text() == "Close at Market"
            )
        )

    # A different Session is being asked for, which is what the question is
    # about: one cannot be made while a Position of the old one is open.
    window.setup.capital_per_entry.setText("2500")
    QTimer.singleShot(0, confirm_close)
    click(window.setup.create_button)
    qtbot.waitUntil(lambda: events == ["close", "retire"])
    qtbot.waitUntil(lambda: len(created) == 1)


def test_an_open_position_shows_before_any_bot_is_started(qtbot: QtBot) -> None:
    """The Basket outlives the Bot, so the tab has one to show on open."""
    session = configured_spot_session()
    relay = ChartFactRelay()
    reopened = reopen_basket(
        OpenBasketState(
            basket_id=UUID("00000000-0000-0000-0000-0000000000d1"),
            entries=(
                OpenBasketEntry(
                    entry_number=1,
                    price=Decimal("100"),
                    quantity=Decimal("2"),
                    filled_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
                    atr=Decimal("2"),
                ),
            ),
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        stop_loss_atr_multiplier=None,
        tick_size=Decimal("0.01"),
    )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(read_open_basket=lambda _session: reopened),
        chart=ChartSources(facts=relay),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._open_position.basket is not None)

    relay.new_generation().in_progress_candle(
        flat_candle(datetime(2026, 8, 1, 12, 5, tzinfo=UTC), 110)
    )

    table = window.workspace.position_basket
    qtbot.waitUntil(lambda: table.table.rowCount() == 1)
    assert table.state_label.text() == ""
    # Read by heading: a column added or dropped must not silently shift
    # which cell this asserts on.
    headings = table.headers
    take_profit = table.table.item(0, headings.index("Take Profit"))
    marked_pnl = table.table.item(0, headings.index("Unrealized P/L"))
    assert take_profit is not None and marked_pnl is not None
    # 100 + 3 ATR, and marked 10 above its Entry Price on a position of 2.
    assert "106" in take_profit.text()
    assert "20" in marked_pnl.text()


def test_a_held_position_survives_a_snapshot_that_carries_no_basket(
    qtbot: QtBot,
) -> None:
    """A stopped Runtime holds nothing, which is not the same as nothing open.

    Its snapshot paints the tab empty, and it goes on arriving after the last
    chart candle — so unless the Basket is put back each time, the tab settles
    on "No open Position" over a Position that is open.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    table = window.workspace.position_basket
    qtbot.waitUntil(lambda: table.table.rowCount() == 1)

    # The same empty snapshot the Runtime publishes while it is stopped, twice
    # over, with no chart candle in between to repaint the tab.
    for _ in range(2):
        window._bot_control_changed(window._lifecycle_workflow.snapshot)

    assert window._open_position.basket is not None
    assert table.table.rowCount() == 1
    assert table.empty_state.isHidden()


def test_a_running_runtime_keeps_the_position_tab_to_itself(qtbot: QtBot) -> None:
    """The Runtime holds the Basket while it runs, and publishes it each candle.

    Putting the window's copy back over that would show a Position as it was
    read from the database rather than as the Bot is trading it.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    table = window.workspace.position_basket
    qtbot.waitUntil(lambda: table.table.rowCount() == 1)

    configured = window._lifecycle_workflow.snapshot
    window._bot_control_changed(
        replace(
            configured,
            state=BotRuntimeState.RUNNING,
            available_actions=frozenset({BotControlAction.STOP}),
            workspace=workspace_with_runtime_state(
                configured.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            ),
        )
    )

    assert window._open_position.basket is None
    assert table.table.rowCount() == 0


def _open_position_window(
    qtbot: QtBot,
    *,
    started: list[BotControlSnapshot],
    asked: list[tuple[str, Callable[[], None]]],
    price: int | None,
    replaceable: bool = False,
    closed: list[str] | None = None,
    second_entry_price: int | None = None,
) -> MainWindow:
    """A stopped Session holding one Entry, with the chart at a given price.

    The Entry is taken at 100 with an ATR of 2 and a multiplier of 3, so its
    target is 106: a price above that is one a Start would sell at. No price
    at all is the state a window opens in, before the chart has said one.

    A second Entry, taken a day later so the Entry Pair lets it in, is what
    makes a Position a Start would sell only part of.
    """
    session = configured_spot_session()
    relay = ChartFactRelay()
    entries: tuple[OpenBasketEntry, ...] = (
        OpenBasketEntry(
            entry_number=1,
            price=Decimal("100"),
            quantity=Decimal("2"),
            filled_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
            atr=Decimal("2"),
        ),
    )
    if second_entry_price is not None:
        entries += (
            OpenBasketEntry(
                entry_number=2,
                price=Decimal(second_entry_price),
                quantity=Decimal("2"),
                filled_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
                atr=Decimal("2"),
            ),
        )
    reopened = reopen_basket(
        OpenBasketState(
            basket_id=UUID("00000000-0000-0000-0000-0000000000d2"),
            entries=entries,
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        stop_loss_atr_multiplier=None,
        tick_size=Decimal("0.01"),
    )

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        started.append(snapshot)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def create(_values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        return PaperSessionCreateOutcome(session=session, created=True)

    # Bound once: an empty list is falsy, so `closed or []` would quietly hand
    # the close a throwaway list to record itself in.
    closes = [] if closed is None else closed

    def read_open_basket(_session: ConfiguredPaperSession) -> ReopenedBasket | None:
        """Gone once it is closed, the way the database answers after a close."""
        return None if closes else reopened

    window = MainWindow(
        create_session=create if replaceable else unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(
            start=start,
            read_open_basket=read_open_basket,
            # What the setup form's Start needs before it offers to replace
            # the Session rather than run the one already there.
            has_open_position=(lambda _session: True) if replaceable else None,
            close_position=(
                (lambda _session: closes.append("closed")) if replaceable else None
            ),
            retire_session=(lambda _session: None) if replaceable else None,
        ),
        chart=ChartSources(facts=relay),
        take_profit_confirmation=lambda message, confirm: asked.append(
            (message, confirm)
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._open_position.basket is not None)
    if price is None:
        return window
    relay.new_generation().in_progress_candle(
        flat_candle(datetime(2026, 8, 1, 12, 5, tzinfo=UTC), price)
    )
    qtbot.waitUntil(
        lambda: window.workspace.position_basket.table.rowCount() == len(entries)
    )
    return window


def test_a_position_no_price_has_reached_yet_is_still_asked_about(
    qtbot: QtBot,
) -> None:
    """An unanswerable question is not the same as one worth skipping.

    The chart had said no price yet, so nothing could tell whether a Start
    would sell at a target. Read as "nothing to ask about", that started the
    Bot onto an open Position and sold it at its target — 63,728.02 against a
    market of 64,194 — with no question put at all.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=None)
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)

    assert len(asked) == 1
    message, confirm = asked[0]
    assert "cannot be read yet" in message
    assert started == []

    confirm()

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(started) == 1


def test_a_position_still_being_read_is_asked_about_before_it_is_started_onto(
    qtbot: QtBot,
) -> None:
    """A read on its way holds no Basket, and that is not an empty one."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    # The window drops what it holds and asks the database again on every
    # stop, which is exactly when a trader reaches for Start.
    window._open_position.read_for(configured_spot_session())
    assert window._open_position.basket is None

    click(control.start_button)

    assert len(asked) == 1
    assert started == []


def test_starting_into_a_reached_take_profit_asks_before_it_sells(
    qtbot: QtBot,
) -> None:
    """The first live tick sells at the target, and a Fill has no way back."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=110)
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)

    assert len(asked) == 1
    message, confirm = asked[0]
    # That the sale books at a target rather than at the price on screen —
    # counted, not priced: the figures are columns in the tab behind it.
    assert message.startswith("1 Entry is already at its Take Profit.")
    assert "at the target it was given, not at the price now" in message
    assert "106" not in message
    assert "110" not in message
    assert started == []

    confirm()

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(started) == 1


def test_declining_the_sale_leaves_the_position_open_and_the_bot_stopped(
    qtbot: QtBot,
) -> None:
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=110)
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)

    assert len(asked) == 1
    assert started == []
    assert window._open_position.basket is not None
    assert _runtime_state(window) is not BotRuntimeState.RUNNING


def test_a_position_short_of_its_target_is_still_said_before_it_is_run_on(
    qtbot: QtBot,
) -> None:
    """Nothing would be sold, and a Position is still being handed to a Bot.

    Silent here and asking on the setup form, the same press would say two
    different things about the same Position depending on which screen it was
    pressed from.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)

    assert len(asked) == 1
    message, confirm = asked[0]
    # What is held, not what a sale would book: this one has reached nothing.
    assert message == "1 Entry is open."
    assert started == []

    confirm()

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(started) == 1


def _answer_position_dialog(label: str, seen: list[QMessageBox]) -> Callable[[], None]:
    """Click a button of the dialog the setup form's Start puts up."""

    def answer() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        seen.append(dialog)
        click(next(button for button in dialog.buttons() if button.text() == label))

    return answer


def test_a_position_at_its_target_is_asked_about_as_a_sale_not_a_replacement(
    qtbot: QtBot,
) -> None:
    """One decision, one dialog, about the thing actually being decided.

    The Position has reached its target, so that — not replacing the Session —
    is what the trader is answering. The only trade offered is the lot at the
    price now, and it is not what a stray Enter picks.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=110,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Close at Market", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: closed == ["closed"])
    assert len(seen) == 1
    dialog = seen[0]
    # The state it is in, said once however many Entries reached a target.
    assert dialog.informativeText().startswith("1 Entry is already at its Take Profit.")
    assert "106" not in dialog.informativeText()
    assert "110" not in dialog.informativeText()
    # The price-now sale is named rather than hidden behind a "Yes", and the
    # answer a stray key hits is the one that trades nothing.
    assert {button.text() for button in dialog.buttons()} == {
        "Close at Market",
        "Cancel",
    }
    assert dialog.defaultButton() is not None
    assert dialog.defaultButton().text() == "Cancel"
    # No second dialog is opened on top of the one just answered.
    assert asked == []


def test_selling_at_a_target_the_price_has_left_is_not_offered(
    qtbot: QtBot,
) -> None:
    """The Bot's own sale would book below the market, so no button makes it.

    Running the Session on would fill the Entry at 106 while 110 is on the
    screen. That trade is gone from the dialog, so the Bot cannot be started
    onto the Position from here at all.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=110,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    click(window.setup.create_button)

    assert len(seen) == 1
    labels = {button.text() for button in seen[0].buttons()}
    assert "Sell at Take Profit" not in labels
    assert not any("Take Profit" in label for label in labels)
    # Nothing on offer resumes the Session onto the Position it holds.
    assert started == []


def test_a_start_that_sells_only_part_of_the_position_still_offers_to_start(
    qtbot: QtBot,
) -> None:
    """One Entry at its target must not close the ones still holding.

    Entry 1 is at 100 with a target of 106, Entry 2 at 110 with a target of
    116, and the price is 112. Only the sale course was reached before, whose
    one way forward — closing at the market price — sold Entry 2 as well.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=112,
        replaceable=True,
        closed=closed,
        second_entry_price=110,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Start Bot", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(seen) == 1
    dialog = seen[0]
    assert {button.text() for button in dialog.buttons()} == {
        "Start Bot",
        "Close at Market",
        "Cancel",
    }
    assert dialog.defaultButton() is not None
    assert dialog.defaultButton().text() == "Cancel"
    # What is held, and the part of it a Start is not free to run on with.
    assert dialog.informativeText().startswith(
        "2 Entries are open. 1 Entry is already at its Take Profit."
    )
    assert "sold at once at the target it was given" in dialog.informativeText()
    # The Entry short of its target is not closed to get the Bot started.
    assert closed == []
    assert len(started) == 1


def test_a_position_wholly_at_its_target_is_still_offered_no_start(
    qtbot: QtBot,
) -> None:
    """Both Entries reached, so running on would book the lot at old prices.

    Entry 2 is taken at 101, whose target of 107 the price of 112 has also
    passed: nothing would be left holding, and Start Bot stays off the box.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=112,
        replaceable=True,
        closed=closed,
        second_entry_price=101,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    click(window.setup.create_button)

    assert len(seen) == 1
    assert {button.text() for button in seen[0].buttons()} == {
        "Close at Market",
        "Cancel",
    }
    assert (
        seen[0]
        .informativeText()
        .startswith("2 Entries are already at their Take Profit.")
    )
    assert started == []
    assert closed == []


def test_cancelling_the_sale_leaves_the_position_and_the_bot_alone(
    qtbot: QtBot,
) -> None:
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=110,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    click(window.setup.create_button)

    assert len(seen) == 1
    assert started == []
    assert closed == []
    assert window._open_position.basket is not None
    assert _runtime_state(window) is not BotRuntimeState.RUNNING


def test_closing_at_market_is_still_offered_and_still_closes(qtbot: QtBot) -> None:
    """The other trade is still reachable — it is only no longer the default."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=110,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Close at Market", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: closed == ["closed"])
    assert asked == []


def test_the_bot_is_started_from_one_place_only(qtbot: QtBot) -> None:
    """Two routes ask the question; only one line answers it by starting.

    A second `start_bot()` somewhere else is how a route ends up starting
    without having asked anything at all.
    """
    source = inspect.getsource(main_window_module.MainWindow)

    assert source.count("self._lifecycle_workflow.start_bot()") == 1
    assert "def _start_now(self) -> None:" in source
    assert "start_bot_requested.connect(self._start_requested)" in source


def test_the_default_take_profit_confirmation_defaults_to_cancel(
    qtbot: QtBot,
) -> None:
    """The answer that cannot be taken back is not the one a stray key hits."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=110)
    # Put the real dialog back, which the helper replaced to drive it by hand.
    window._take_profit_confirmation = window._open_take_profit_confirmation
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)

    dialog = window.findChild(QMessageBox)
    assert dialog is not None
    cancel = next(button for button in dialog.buttons() if button.text() == "Cancel")
    assert dialog.defaultButton() is cancel
    assert dialog.escapeButton() is cancel
    assert "1 Entry is already at its Take Profit." in dialog.informativeText()
    assert started == []

    click(cancel)

    qtbot.waitUntil(lambda: window.findChild(QMessageBox) is None)
    assert started == []


def test_a_position_that_cannot_be_read_back_says_so_instead_of_nothing(
    qtbot: QtBot,
) -> None:
    """ "No open Position" would be the one answer that is not true."""

    def unreadable(_session: ConfiguredPaperSession) -> ReopenedBasket | None:
        raise PaperPositionRestoreError("open Basket has an Entry without its ATR")

    window = MainWindow(
        create_session=unused_create,
        load_active=configured_spot_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(read_open_basket=unreadable),
    )
    qtbot.addWidget(window)
    window.show()

    state_label = window.workspace.position_basket.state_label
    qtbot.waitUntil(lambda: state_label.text() == POSITION_BASKET_UNREADABLE_MESSAGE)
    assert "Start Paper Bot" in state_label.text()
    assert window.workspace.position_basket.table.rowCount() == 0


def test_an_untouched_form_runs_the_session_on_once_that_is_answered_for(
    qtbot: QtBot,
) -> None:
    """Start Bot is the answer that closes nothing and creates nothing.

    The form still says exactly what the Session says, so running it on is
    all that is left to do — but it is still an open Position being handed
    back to a Bot, and the answer that does it is picked rather than assumed.
    """
    session = configured_spot_session()
    created: list[PaperSessionSetupValues] = []
    events: list[str] = []
    start_calls: list[UUID] = []

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        start_calls.append(snapshot.session.config.session_id)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def stop(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace, BotRuntimeState.STOPPED
            )
        )

    def create(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        created.append(values)
        return PaperSessionCreateOutcome(session=session, created=True)

    window = MainWindow(
        create_session=create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(
            start=start,
            stop=stop,
            close_position=lambda _session: events.append("close"),
            has_open_position=lambda _session: True,
            retire_session=lambda _session: events.append("retire"),
        ),
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    click(control.stop_button)
    stop_dialog = window.findChild(QMessageBox)
    assert stop_dialog is not None
    click(
        next(button for button in stop_dialog.buttons() if button.text() == "Stop Bot")
    )
    qtbot.waitUntil(window.setup.isVisible)
    assert start_calls == [session.config.session_id]
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Start Bot", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(seen) == 1
    # The same Session, run on: nothing closed, retired, or created.
    assert start_calls == [session.config.session_id] * 2
    assert events == []
    assert created == []


def test_an_edited_form_still_asks_before_it_closes_the_position(
    qtbot: QtBot,
) -> None:
    """Asking for different values is asking for the Position to be closed."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        # Below the Entry's target of 106, so the sale is not what is at stake.
        price=105,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[str] = []

    def close_at_market() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        seen.append(dialog.informativeText())
        click(
            next(
                button
                for button in dialog.buttons()
                if button.text() == "Close at Market"
            )
        )

    window.setup.capital_per_entry.setText("2500")
    QTimer.singleShot(0, close_at_market)
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: closed == ["closed"])
    assert len(seen) == 1
    assert "sells the whole Position at the price now" in seen[0]
    # How much is open, and no figure the Position tab behind it already
    # shows: what it was paid, what it is worth, where its target is.
    assert seen[0].startswith("1 Entry is open.")
    assert "100" not in seen[0]
    assert "105" not in seen[0]
    assert "106" not in seen[0]


def test_the_answer_that_sells_at_market_is_not_the_default(qtbot: QtBot) -> None:
    """This question, with Yes as its default, sold two Positions by accident.

    Every answer is named after what it does to the Position, and the one a
    stray Enter or Escape finds is the one that does nothing to it.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        # Below the Entry's target of 106: the Take Profit question is not
        # the one being put here.
        price=105,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    buttons: list[tuple[str, str, str]] = []

    def read_and_keep() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        default = dialog.defaultButton()
        escape = dialog.escapeButton()
        assert default is not None and escape is not None
        buttons.append(
            (
                " · ".join(sorted(button.text() for button in dialog.buttons())),
                default.text(),
                escape.text(),
            )
        )
        click(default)

    window.setup.capital_per_entry.setText("2500")
    QTimer.singleShot(0, read_and_keep)
    click(window.setup.create_button)

    assert buttons == [("Cancel · Close at Market · Start Bot", "Cancel", "Cancel")]
    # Cancel is what it says: nothing was sold, and no Bot was started.
    assert closed == []
    assert started == []


def test_starting_on_the_position_closes_nothing_and_creates_nothing(
    qtbot: QtBot,
) -> None:
    """The answer that runs the Session on is the answer that trades nothing."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=105,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    # Edited, and then answered with the Session rather than the form: the
    # values go back to the ones the Session actually holds.
    window.setup.capital_per_entry.setText("2500")
    QTimer.singleShot(0, _answer_position_dialog("Start Bot", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(seen) == 1
    assert closed == []
    assert len(started) == 1
    # The Runtime holds the Basket once it runs, which is what this window
    # handed to it rather than sold.
    assert window._open_position.basket is None
    # No second question on top of the one just answered.
    assert asked == []


def test_cancelling_the_position_question_leaves_it_and_the_bot_alone(
    qtbot: QtBot,
) -> None:
    """Cancel is the answer a stray key finds, so it must do nothing at all."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=105,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    click(window.setup.create_button)

    assert len(seen) == 1
    assert started == []
    assert closed == []
    assert window._open_position.basket is not None
    assert _runtime_state(window) is not BotRuntimeState.RUNNING


def test_start_from_active_session_reports_missing_replacement_service(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.setup.capital_per_entry.text() != "")

    click(window.setup.create_button)

    assert "replacement is unavailable" in window.setup.capital_per_entry_error.text()


def test_configured_session_does_not_auto_start_and_explains_missing_runtime(
    qtbot: QtBot,
) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=configured_spot_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._replacement_session is not None)

    assert window.setup.create_button.text() == "Start Paper Bot"
    assert window.setup.create_button.isEnabled()
    assert window.workspace.overview.isVisible() is False


def _session_with_policy_changes(
    session: ConfiguredPaperSession,
    **changes: object,
) -> ConfiguredPaperSession:
    inconsistent_config = copy(session.config)
    for field, value in changes.items():
        object.__setattr__(inconsistent_config, field, value)
    return replace(session, config=inconsistent_config)
