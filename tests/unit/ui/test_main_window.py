import ast
import inspect
import threading
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
from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tests.support.qt_interactions import click, qdate
from tests.support.trade_history_records import basket_result, trade_fill
from tests.support.trade_history_ui import empty_basket_page, empty_fills
from tiewtrade.application.bot_control import (
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
    PaperSessionUnavailableError,
    PaperSessionValidationError,
)
from tiewtrade.application.trade_history import (
    BasketHistoryPage,
    PageRequest,
    TradeHistoryFilter,
)
from tiewtrade.application.trading_workspace import (
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
from tiewtrade.trading.spot_policy import SpotTradingPolicy
from tiewtrade.trading.trade_history import TradeFill
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.main_window import MainWindow
from tiewtrade.ui.session_overview import SessionOverviewWidget


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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        load_chart=load_chart,
        chart_clock=lambda: datetime(2026, 8, 2, 10, 3, tzinfo=UTC),
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        load_chart=load_chart,
        select_chart_view=lambda _session, selection: feed_selections.append(selection),
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        load_chart=unavailable_chart,
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
        lambda: window.trade_history.basket_state.text() == "No trade history"
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
    window.workspace.tabs.setCurrentWidget(window.trade_history)


def _widget_rect_in_viewport(widget: QWidget, viewport: QWidget) -> QRect:
    return QRect(widget.mapTo(viewport, QPoint()), widget.size())


def _session_configuration_controls(window: MainWindow) -> tuple[QWidget, ...]:
    setup = window.setup
    return (
        setup.market_type,
        setup.symbol,
        setup.timeframe,
        setup.strategy,
        setup.available_capital,
        setup.max_entries,
        setup.spot_ratio,
        setup.leverage,
        setup.advanced_toggle,
        setup.fee_percent,
        setup.slippage_bps,
        setup.create_button,
    )


def test_trade_history_opens_without_active_session(qtbot: QtBot) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    open_trade_history(window)

    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No trade history"
    )
    assert window.trade_history.isVisible()
    # No session yet: the setup form is what the workspace offers.
    assert window.setup.isVisible()


def test_empty_trade_history_preserves_exact_query_summary_and_page_state(
    qtbot: QtBot,
) -> None:
    exact_empty_page = BasketHistoryPage(
        items=(),
        page=1,
        page_size=50,
        total_items=0,
        net_realized_pnl=Decimal("0.000000000000000001"),
    )
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=lambda filters, request: exact_empty_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    open_trade_history(window)

    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No trade history"
    )
    assert (
        window.trade_history.total_net_pnl.text()
        == "0.000000000000000001 USDT · Profit"
    )
    assert window.trade_history.total_items.text() == "0 total Baskets"
    assert window.trade_history.page_label.text() == "Page 1 of 1"


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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.unavailable_panel.isVisible)

    open_trade_history(window)

    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No trade history"
    )


def test_tab_activation_starts_history_query_only_once(qtbot: QtBot) -> None:
    calls = 0

    def count_baskets(
        filters: TradeHistoryFilter, request: PageRequest
    ) -> BasketHistoryPage:
        nonlocal calls
        calls += 1
        return empty_basket_page(filters, request)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=count_baskets,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    open_trade_history(window)
    qtbot.waitUntil(lambda: calls == 1)
    window.workspace.tabs.setCurrentIndex(0)
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(started.is_set)

    open_trade_history(window)
    release.set()

    qtbot.waitUntil(
        lambda: (
            window.overview.symbol_value.text() == existing.market_data.symbol
        )
    )
    assert window.trade_history.isVisible()
    assert window.overview.isVisible() is False
    assert _runtime_state(window) is BotRuntimeState.CONFIGURED


def test_trade_history_basket_and_fill_retries_are_wired_separately(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    fill = trade_fill()
    basket_calls = 0
    fill_calls = 0

    def fail_baskets_once(
        filters: TradeHistoryFilter,
        request: PageRequest,
    ) -> BasketHistoryPage:
        nonlocal basket_calls
        basket_calls += 1
        if basket_calls == 1:
            raise RuntimeError("private basket failure")
        return BasketHistoryPage(
            items=(basket,),
            page=request.page,
            page_size=request.page_size,
            total_items=1,
            net_realized_pnl=Decimal("0"),
        )

    def fail_fills_once(basket_id: UUID) -> tuple[TradeFill, ...]:
        nonlocal fill_calls
        fill_calls += 1
        if fill_calls == 1:
            raise RuntimeError("private fill failure")
        assert basket_id == basket.basket_id
        return (fill,)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=fail_baskets_once,
        list_fills=fail_fills_once,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "Trade History unavailable"
    )

    click(window.trade_history.retry_baskets_button)
    qtbot.waitUntil(
        lambda: window.trade_history.fill_state.text() == "Trade Fills unavailable"
    )

    click(window.trade_history.retry_fills_button)
    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 1)
    assert basket_calls == 2
    assert fill_calls == 2


def test_fill_retry_remains_wired_after_basket_refresh_failure(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    fill = trade_fill()
    basket_calls = 0
    fill_calls = 0

    def succeed_then_fail_baskets(
        filters: TradeHistoryFilter,
        request: PageRequest,
    ) -> BasketHistoryPage:
        nonlocal basket_calls
        del filters
        basket_calls += 1
        if basket_calls > 1:
            raise RuntimeError("private basket failure")
        return BasketHistoryPage(
            items=(basket,),
            page=request.page,
            page_size=request.page_size,
            total_items=1,
            net_realized_pnl=basket.net_realized_pnl,
        )

    def succeed_fail_succeed_fills(basket_id: UUID) -> tuple[TradeFill, ...]:
        nonlocal fill_calls
        fill_calls += 1
        assert basket_id == basket.basket_id
        if fill_calls == 2:
            raise RuntimeError("private fill failure")
        return (fill,)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=succeed_then_fail_baskets,
        list_fills=succeed_fail_succeed_fills,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 1)

    window.trade_history.basket_selected.emit(basket.basket_id)
    qtbot.waitUntil(
        lambda: (
            window.trade_history.fill_state.text() == "Stale · Trade Fills unavailable"
        )
    )
    click(window.trade_history.apply_button)
    qtbot.waitUntil(
        lambda: (
            window.trade_history.basket_state.text()
            == "Stale · Trade History unavailable"
        )
    )

    window.trade_history.history_tabs.setCurrentIndex(1)
    assert window.trade_history.retry_fills_button.isVisible()
    click(window.trade_history.retry_fills_button)

    qtbot.waitUntil(lambda: fill_calls == 3)
    qtbot.waitUntil(window.trade_history.fill_state.isHidden)
    assert basket_calls == 2


def test_invalidated_active_fill_does_not_keep_loading_after_basket_failure(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    fill = trade_fill()
    basket_calls = 0
    fill_calls = 0
    fill_started = threading.Event()
    release_fill = threading.Event()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(2)

    def succeed_then_fail_baskets(
        filters: TradeHistoryFilter,
        request: PageRequest,
    ) -> BasketHistoryPage:
        nonlocal basket_calls
        del filters
        basket_calls += 1
        if basket_calls > 1:
            raise RuntimeError("private basket failure")
        return BasketHistoryPage(
            items=(basket,),
            page=request.page,
            page_size=request.page_size,
            total_items=1,
            net_realized_pnl=basket.net_realized_pnl,
        )

    def delay_second_fill(basket_id: UUID) -> tuple[TradeFill, ...]:
        nonlocal fill_calls
        fill_calls += 1
        assert basket_id == basket.basket_id
        if fill_calls == 2:
            fill_started.set()
            assert release_fill.wait(timeout=1)
        return (fill,)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=succeed_then_fail_baskets,
        list_fills=delay_second_fill,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 1)

    window.trade_history.basket_selected.emit(basket.basket_id)
    qtbot.waitUntil(fill_started.is_set)
    qtbot.waitUntil(
        lambda: window.trade_history.fill_state.text() == "Loading trade fills…"
    )
    assert not window.trade_history.apply_button.isEnabled()
    release_fill.set()
    qtbot.waitUntil(window.trade_history.apply_button.isEnabled)
    click(window.trade_history.apply_button)
    qtbot.waitUntil(
        lambda: (
            window.trade_history.basket_state.text()
            == "Stale · Trade History unavailable"
        )
    )

    try:
        assert window.trade_history.fill_state.text() != "Loading trade fills…"
        assert window.trade_history.fill_state.isHidden()
        assert window.trade_history.fill_table.rowCount() == 1
    finally:
        release_fill.set()
        assert thread_pool.waitForDone(1_000)


def test_trade_history_filter_reset_page_and_selection_requests_are_wired(
    qtbot: QtBot,
) -> None:
    first = basket_result()
    second = basket_result(basket_id=UUID("00000000-0000-0000-0000-000000000202"))
    basket_calls: list[tuple[TradeHistoryFilter, PageRequest]] = []
    fill_calls: list[UUID] = []

    def list_baskets(
        filters: TradeHistoryFilter,
        request: PageRequest,
    ) -> BasketHistoryPage:
        basket_calls.append((filters, request))
        return BasketHistoryPage(
            items=(first, second) if request.page == 1 else (second,),
            page=request.page,
            page_size=request.page_size,
            total_items=51,
            net_realized_pnl=Decimal("0"),
        )

    def list_fills(basket_id: UUID) -> tuple[TradeFill, ...]:
        fill_calls.append(basket_id)
        return ()

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=list_baskets,
        list_fills=list_fills,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 2)
    qtbot.waitUntil(lambda: fill_calls == [first.basket_id])

    window.trade_history.basket_table.selectRow(1)
    qtbot.waitUntil(lambda: fill_calls[-1] == second.basket_id)
    window.trade_history.symbol.setCurrentIndex(
        window.trade_history.symbol.findData("BTCUSDT")
    )
    click(window.trade_history.apply_button)
    qtbot.waitUntil(lambda: len(basket_calls) == 2)
    assert basket_calls[-1] == (
        TradeHistoryFilter(symbol="BTCUSDT"),
        PageRequest(page=1, page_size=50),
    )

    click(window.trade_history.reset_button)
    qtbot.waitUntil(lambda: len(basket_calls) == 3)
    assert basket_calls[-1] == (
        TradeHistoryFilter(),
        PageRequest(page=1, page_size=50),
    )

    qtbot.waitUntil(window.trade_history.next_button.isEnabled)
    click(window.trade_history.next_button)
    qtbot.waitUntil(lambda: len(basket_calls) == 4)
    assert basket_calls[-1] == (
        TradeHistoryFilter(),
        PageRequest(page=2, page_size=50),
    )


def test_trade_history_filter_validation_is_wired_without_starting_query(
    qtbot: QtBot,
) -> None:
    basket_calls = 0

    def count_baskets(
        filters: TradeHistoryFilter,
        request: PageRequest,
    ) -> BasketHistoryPage:
        nonlocal basket_calls
        basket_calls += 1
        return empty_basket_page(filters, request)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=count_baskets,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    open_trade_history(window)
    qtbot.waitUntil(lambda: basket_calls == 1)
    qtbot.waitUntil(window.trade_history.apply_button.isEnabled)
    window.trade_history.from_date_enabled.setChecked(True)
    window.trade_history.to_date_enabled.setChecked(True)
    window.trade_history.from_date.setDate(qdate(date(2026, 1, 2)))
    window.trade_history.to_date.setDate(qdate(date(2026, 1, 1)))

    click(window.trade_history.apply_button)

    qtbot.waitUntil(window.trade_history.filter_error.isVisible)
    assert (
        window.trade_history.filter_error.text()
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("200000")

    click(window.setup.create_button)

    qtbot.waitUntil(lambda: window._replacement_session == created)
    assert window.setup.market_type.currentData() == "spot"
    assert window.setup.timeframe.currentData() == "5m"
    assert window.setup.available_capital.text() == "200000"
    assert window.setup.spot_reserve_ratio.text() == "20%"


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


def test_futures_overview_marks_mixed_spot_policy_unavailable(
    qtbot: QtBot,
) -> None:
    session = _session_with_policy_changes(
        configured_futures_session(),
        spot_policy=SpotTradingPolicy(Decimal("0.8")),
    )
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(session)

    assert overview.margin_mode_value.text() == "Unavailable"
    assert overview.position_mode_value.text() == "Unavailable"


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"spot_policy": None}, id="missing-spot-policy"),
        pytest.param(
            {"futures_policy": FuturesTradingPolicy.v1(leverage=3)},
            id="mixed-futures-policy",
        ),
    ],
)
def test_spot_overview_marks_missing_or_mixed_policy_unavailable(
    qtbot: QtBot,
    changes: dict[str, object],
) -> None:
    session = _session_with_policy_changes(configured_spot_session(), **changes)
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(session)

    assert overview.spot_ratio_value.text() == "Unavailable"
    assert overview.spot_reserve_ratio_value.text() == "Unavailable"


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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("200000")

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
            "available_capital", "Available Capital must be positive"
        )

    window = MainWindow(
        create_session=reject,
        load_active=no_active_session,
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("0")

    click(window.setup.create_button)

    qtbot.waitUntil(
        lambda: (
            window.setup.available_capital_error.text()
            == "Available Capital must be positive"
        )
    )
    assert window.setup.isVisible()
    assert window.setup.create_button.isEnabled() is True
    assert window.setup.available_capital.text() == "0"


def test_validation_failure_reveals_and_focuses_advanced_field(
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    window.setup.available_capital.setText("200000")
    click(window.setup.advanced_toggle)
    window.setup.fee_percent.setText("100")
    window.setup.slippage_bps.setText("5")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)

    qtbot.waitUntil(
        lambda: (
            window.setup.fee_percent_error.text() == "Trading Fee must be below 100%"
        )
    )
    viewport = window.workspace.bot_control_scroll.viewport()

    assert window.workspace.bot_control.isVisible()
    assert window.setup.fee_percent_error.isVisible()
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.fee_percent, viewport)
    )
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.fee_percent_error, viewport)
    )
    assert window.setup.fee_percent.hasFocus()


@pytest.mark.parametrize("field", ["fee_percent", "slippage_bps"])
def test_required_advanced_validation_expands_and_reveals_field(
    qtbot: QtBot,
    field: str,
) -> None:
    def unused_create_active(
        _session: ConfiguredPaperSession,
    ) -> PaperSessionCreateOutcome:
        pytest.fail("invalid setup must not create a session")

    create_session = CreatePaperSession(create_active=unused_create_active)
    window = MainWindow(
        create_session=create_session.execute,
        load_active=no_active_session,
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    assert not window.setup.advanced_toggle.isChecked()

    window.setup.available_capital.setText("200000")
    window.setup.fee_percent.clear()
    window.setup.slippage_bps.clear()
    if field == "slippage_bps":
        window.setup.fee_percent.setText("0.1")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)

    control = getattr(window.setup, field)
    error_label = getattr(window.setup, f"{field}_error")
    qtbot.waitUntil(lambda: error_label.text() == "This field is required")
    viewport = window.workspace.bot_control_scroll.viewport()

    assert window.setup.advanced_toggle.isChecked()
    assert window.setup.advanced_costs.isVisible()
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("200000")
    click(window.setup.advanced_toggle)
    window.setup.fee_percent.setText("100")
    window.setup.slippage_bps.setText("5")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)
    qtbot.waitUntil(started.is_set)

    release.set()

    qtbot.waitUntil(
        lambda: (
            window.setup.fee_percent_error.text() == "Trading Fee must be below 100%"
        )
    )
    viewport = window.workspace.bot_control_scroll.viewport()

    assert window.workspace.bot_control.isVisible()
    assert window.setup.fee_percent_error.isVisible()
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.fee_percent, viewport)
    )
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.fee_percent_error, viewport)
    )
    assert window.setup.fee_percent.hasFocus()
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        thread_pool=thread_pool,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("200000")
    window.setup.spot_ratio.setText("100")
    click(window.setup.advanced_toggle)
    window.setup.fee_percent.setText("0.1")
    window.setup.slippage_bps.setText("5")
    window.workspace.bot_control_scroll.ensureWidgetVisible(window.setup.create_button)
    click(window.setup.create_button)
    qtbot.waitUntil(started.is_set)

    controls = _session_configuration_controls(window)
    assert all(not control.isEnabled() for control in controls)
    QTest.keyClick(window.setup.market_type, Qt.Key.Key_Down)
    assert window.setup.market_type.currentData() == "spot"
    release.set()

    qtbot.waitUntil(
        lambda: (
            window.setup.spot_ratio_error.text()
            == "trading_capital_ratio must be between 0 and 1"
        )
    )
    qtbot.waitUntil(lambda: all(control.isEnabled() for control in controls))
    viewport = window.workspace.bot_control_scroll.viewport()
    assert window.setup.market_type.currentData() == "spot"
    assert window.setup.spot_ratio.text() == "100"
    assert window.setup.spot_ratio_error.isVisible()
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.spot_ratio, viewport)
    )
    assert viewport.rect().contains(
        _widget_rect_in_viewport(window.setup.spot_ratio_error, viewport)
    )
    assert window.setup.spot_ratio.hasFocus()
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("200000")

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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
            PaperSessionValidationError("available_capital", "invalid persisted value"),
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(window.unavailable_panel.isVisible)

    assert window.unavailable_message.text() == expected_message
    assert not window.setup.isVisible()
    assert window.setup.available_capital_error.text() == ""
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        shutdown_runtime=shutdown_runtime,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        stop_chart_feed=lambda: stopped.append(True),
    )
    qtbot.addWidget(window)

    window._close_workflows()

    assert stopped == []


def test_closing_window_ignores_late_trade_history_result(qtbot: QtBot) -> None:
    started = threading.Event()
    release = threading.Event()
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)

    def delayed_baskets(
        filters: TradeHistoryFilter,
        request: PageRequest,
    ) -> BasketHistoryPage:
        started.set()
        if not release.wait(timeout=1):
            raise TimeoutError("test did not release worker")
        return empty_basket_page(filters, request)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_baskets=delayed_baskets,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=start_bot,
        stop_bot=stop_bot,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.workspace.bot_control_widget.start_button.isEnabled()
    )

    click(window.workspace.bot_control_widget.start_button)
    click(window.workspace.bot_control_widget.start_button)

    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.RUNNING
    )
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

    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.STOPPED
    )
    assert stop_calls == 1


def test_blocked_runtime_reopens_the_editable_setup_form(
    qtbot: QtBot,
) -> None:
    def fail_start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        raise RuntimeError("raw fake exception must not reach the UI")

    window = MainWindow(
        create_session=unused_create,
        load_active=configured_spot_session,
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=fail_start,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.workspace.bot_control_widget.start_button.isEnabled()
    )

    click(window.workspace.bot_control_widget.start_button)

    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.BLOCKED
    )
    qtbot.waitUntil(window.setup.isVisible)
    assert window.setup.available_capital.isEnabled()


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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=start,
        stop_bot=stop,
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)
    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.RUNNING
    )
    click(control.stop_button)
    stop_confirmation = window.findChild(QMessageBox)
    assert stop_confirmation is not None
    stop_button = next(
        button for button in stop_confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(stop_button)
    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.STOPPED
    )

    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.timeframe.isEnabled)
    assert window.setup.timeframe.currentData() == "5m"
    assert window.setup.available_capital.isEnabled()


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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=start,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("1000")

    click(window.setup.create_button)

    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.RUNNING
    )
    assert len(created) == 1
    assert created[0].available_capital == "1000"
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=start,
        initialize_bot=initialize,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    window.setup.available_capital.setText("1000")

    click(window.setup.create_button)

    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.RUNNING
    )
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=start,
        stop_bot=stop,
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.RUNNING
    )
    click(control.stop_button)
    confirmation = window.findChild(QMessageBox)
    assert confirmation is not None
    stop_button = next(
        button for button in confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(stop_button)

    qtbot.waitUntil(window.setup.isVisible)
    assert retired == []
    assert window.setup.available_capital.text() == format(
        session.config.available_capital, "f"
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
        total_quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
        current_price=Decimal("110"),
        take_profit_price=Decimal("120"),
        unrealized_pnl=Decimal("10"),
        liquidation_price=None,
        lifecycle="open",
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        start_bot=start,
        stop_bot=stop,
        close_position=close_position,
        has_open_position=lambda _session: True,
        retire_session=retire_session,
        runtime_snapshots=runtime_snapshots,
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(
        lambda: _runtime_state(window) is BotRuntimeState.RUNNING
    )
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
        yes = dialog.button(QMessageBox.StandardButton.Yes)
        assert yes is not None
        click(yes)

    QTimer.singleShot(0, confirm_close)
    click(window.setup.create_button)
    qtbot.waitUntil(lambda: events == ["close", "retire"])
    qtbot.waitUntil(lambda: len(created) == 1)


def test_start_from_active_session_reports_missing_replacement_service(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.setup.available_capital.text() != "")

    click(window.setup.create_button)

    assert "replacement is unavailable" in window.setup.available_capital_error.text()


def test_configured_session_does_not_auto_start_and_explains_missing_runtime(
    qtbot: QtBot,
) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=configured_spot_session,
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
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
