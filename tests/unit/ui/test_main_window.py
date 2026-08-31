import ast
import inspect
import itertools
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
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QPushButton,
    QWidget,
)
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
from tests.support.workspace_snapshots import basket_entries, basket_targets
from tiewtrade.application.bot_control import (
    BotControlAction,
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.chart_data import (
    ChartFeedState,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
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
    paper_runtime_blocked_workspace_snapshot,
    paper_runtime_workspace_snapshot,
    stale_workspace_snapshot,
)
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import TradeFill
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.chart_workflow import ChartFactRelay
from tiewtrade.ui.display_time import display_today
from tiewtrade.ui.main_window import (
    CHART_FEED_STOPPED_NOTICE,
    BotActions,
    ChartSources,
    MainWindow,
)
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


def test_setup_screen_loads_a_chart_for_the_default_session(qtbot: QtBot) -> None:
    """No Bot Session exists yet, but the chart still has something to show."""
    default_session = configured_spot_session()
    requests: list[tuple[ConfiguredPaperSession, ChartRange]] = []
    feed_started: list[ConfiguredPaperSession] = []

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
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            load=load_chart,
            start_feed=feed_started.append,
            default_session=default_session,
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    qtbot.waitUntil(lambda: len(requests) == 1)
    configured, _chart_range = requests[0]
    assert configured is default_session
    assert feed_started == [default_session]


def test_setup_screen_chart_view_selection_reloads_before_any_session_exists(
    qtbot: QtBot,
) -> None:
    """The Setup screen's own symbol/timeframe combos must not be dead controls."""
    default_session = configured_spot_session()
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
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            load=load_chart,
            select_view=lambda _session, selection: feed_selections.append(selection),
            default_session=default_session,
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


def test_creating_a_session_replaces_the_default_chart_preview(qtbot: QtBot) -> None:
    """A real Session's own chart must take over from the placeholder cleanly."""
    default_session = configured_spot_session()
    real_session = configured_spot_session(session_id=UUID(int=99))
    requests: list[ConfiguredPaperSession] = []

    async def load_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        requests.append(configured)
        return ChartSnapshot(
            session=configured,
            chart_range=chart_range,
            observed_at_utc=chart_range.end,
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
        )

    window = MainWindow(
        create_session=lambda _values: PaperSessionCreateOutcome(real_session, True),
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(load=load_chart, default_session=default_session),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: requests == [default_session])

    window._workflow.create(
        PaperSessionSetupValues(
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="5m",
            capital_per_entry="20000",
            max_entries="10",
            futures_leverage=None,
        )
    )

    qtbot.waitUntil(lambda: requests[-1] is real_session)


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


def wait_for_history_reads_to_land(qtbot: QtBot, window: MainWindow) -> None:
    """Wait until no history read is still in flight.

    The counters these tests keep are raised by the read itself, on the
    thread it runs on, so a count says a read has *started*. A tab reopened
    while one is still running is answered by that one rather than starting
    another — the de-duplication these tests are not about — so waiting on
    the count alone lets the reopen land too early and the next count never
    comes.
    """
    workflow = window._history_workflow
    qtbot.waitUntil(lambda: not workflow._basket_tasks and not workflow._trade_tasks)


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


def test_reopening_a_history_tab_reads_it_again(qtbot: QtBot) -> None:
    """A visit to the tab is a request to see what is there now.

    Records land while the trader is on the Position tab — every Entry fill
    of an open Basket — so the half being opened is read again each time,
    not left showing what it held on the first visit.
    """
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
    wait_for_history_reads_to_land(qtbot, window)
    window.workspace.show_tab(WorkspaceTab.POSITION_BASKET)
    open_trade_history(window)

    qtbot.waitUntil(lambda: calls == 2)


def test_reopening_the_trade_half_reads_it_again_without_doubling(
    qtbot: QtBot,
) -> None:
    """Reopening the other half asks for it once, not once per signal."""
    calls = 0

    def count_fills(filters: TradeHistoryFilter) -> tuple[TradeFillHistoryItem, ...]:
        nonlocal calls
        calls += 1
        return empty_trade_fills(filters)

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=count_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    qtbot.waitUntil(lambda: calls == 1)
    wait_for_history_reads_to_land(qtbot, window)
    window.workspace.show_tab(WorkspaceTab.POSITION_BASKET)
    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)

    qtbot.waitUntil(lambda: calls == 2)
    qtbot.wait(50)
    assert calls == 2


def test_position_history_refreshes_when_the_open_basket_closes(
    qtbot: QtBot,
) -> None:
    """A closed Basket is a new row nothing else asks for again on its own.

    The table is read once when the tab opens; from there it is the close
    itself, not another visit to the tab, that has to ask for it again.
    """
    calls = 0

    def count_baskets(filters: TradeHistoryFilter) -> BasketHistoryResult:
        nonlocal calls
        calls += 1
        return empty_basket_history(filters)

    session = configured_spot_session()
    relay = ChartFactRelay()
    sold: list[tuple[str, int]] = []
    basket_open = True

    def read_open_basket(_session: ConfiguredPaperSession) -> ReopenedBasket | None:
        return _basket_of(session, "100") if basket_open else None

    def close_entry(current_session: ConfiguredPaperSession, entry_number: int) -> bool:
        nonlocal basket_open
        sold.append((current_session.market_data.symbol, entry_number))
        basket_open = False
        return True

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=count_baskets,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(read_open_basket=read_open_basket, close_entry=close_entry),
        chart=ChartSources(facts=relay),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._open_position.basket is not None)
    relay.new_generation().in_progress_candle(
        flat_candle(datetime(2026, 8, 1, 12, 5, tzinfo=UTC), 105)
    )
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)

    open_trade_history(window)
    qtbot.waitUntil(lambda: calls == 1)
    wait_for_history_reads_to_land(qtbot, window)
    window.workspace.show_tab(WorkspaceTab.POSITION_BASKET)

    QTimer.singleShot(0, _answer_position_dialog("Market", []))
    _click_close_entry(window, 0)

    qtbot.waitUntil(lambda: sold == [("BTCUSDT", 1)])
    qtbot.waitUntil(lambda: calls == 2)


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
    # The one Futures setting the trader chose, and the multiplier that says
    # what the Amount beside it actually opens.
    assert overview.leverage_value.text() == "3x"
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

    assert overview.leverage_value.text() == "Unavailable"
    assert overview.margin_mode_value.text() == "Unavailable"
    assert overview.position_mode_value.text() == "Unavailable"


def test_spot_overview_marks_a_mixed_futures_policy_unavailable(
    qtbot: QtBot,
) -> None:
    """A Spot Session carries no policy, so holding one is malformed.

    The rows are laid out for this one — a Session assembled wrongly is worth
    a row that says so, rather than one the card quietly leaves out.
    """
    session = _session_with_policy_changes(
        configured_spot_session(),
        futures_policy=FuturesTradingPolicy.v1(leverage=3),
    )
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(session)

    assert "Leverage" in overview.visible_labels
    assert overview.leverage_value.text() == "Unavailable"
    assert overview.margin_mode_value.text() == "Unavailable"
    assert overview.position_mode_value.text() == "Unavailable"


def test_spot_overview_leaves_the_futures_rows_out(qtbot: QtBot) -> None:
    """Spot has no leverage at all, so the card does not keep a row for it.

    Three rows reading blank take half the card to repeat what the Market
    Type at the top of it already said.
    """
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(configured_spot_session())

    assert overview.visible_labels == (
        "Market Type",
        "Pair",
        "Timeframe",
        "Strategy Preset",
        "Amount",
        "Max Entries",
        "Created at",
    )
    for value in (
        overview.leverage_value,
        overview.margin_mode_value,
        overview.position_mode_value,
    ):
        assert value.isHidden()
        # A row out of the layout holds nothing either, so text meant for a
        # market this Session is not in cannot be read off it.
        assert value.text() == "—"
    assert not hasattr(overview, "spot_ratio_value")
    assert not hasattr(overview, "spot_reserve_ratio_value")


def test_futures_overview_keeps_the_policy_rows(qtbot: QtBot) -> None:
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(configured_futures_session(leverage=3))

    assert overview.visible_labels == (
        "Market Type",
        "Pair",
        "Timeframe",
        "Strategy Preset",
        "Amount",
        "Max Entries",
        "Leverage",
        "Margin Mode",
        "Position Mode",
        "Created at",
    )


def test_the_policy_rows_come_and_go_with_the_market_shown(qtbot: QtBot) -> None:
    """One widget shows both markets, so the rows have to be taken out and
    put back rather than filled in and blanked."""
    overview = SessionOverviewWidget()
    qtbot.addWidget(overview)

    overview.show_session(configured_futures_session(leverage=3))
    overview.show_session(configured_spot_session())

    assert "Leverage" not in overview.visible_labels
    assert overview.leverage_value.isHidden()

    overview.show_session(configured_futures_session(leverage=3))

    assert "Leverage" in overview.visible_labels
    assert overview.leverage_value.text() == "3x"


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


def test_a_runtime_that_dies_while_running_says_so_before_the_setup_form(
    qtbot: QtBot,
) -> None:
    """The Bot stopping on its own is news, and the window used to swallow it.

    A Runtime that fails closed takes the workspace back to the setup form
    with nothing said, which reads as the application forgetting the Session
    rather than as the Bot having stopped.
    """
    session = configured_spot_session()
    runtime_snapshots = RuntimeSnapshotRelay()
    notices: list[str] = []
    reason = "Paper Bot stopped: market data could not be recovered"

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start, runtime_snapshots=runtime_snapshots),
        blocked_notice=notices.append,
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)

    runtime_snapshots.new_generation()(
        BotLifecycleResult(
            workspace=paper_runtime_blocked_workspace_snapshot(
                window._lifecycle_workflow.snapshot.workspace
            ),
            blocked_reason=reason,
        )
    )

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.BLOCKED)
    qtbot.waitUntil(window.setup.isVisible)
    assert notices == [reason]


def test_a_bot_that_never_started_reopens_the_setup_form_without_a_notice(
    qtbot: QtBot,
) -> None:
    def fail_start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        raise RuntimeError("raw fake exception must not reach the UI")

    notices: list[str] = []
    window = MainWindow(
        create_session=unused_create,
        load_active=configured_spot_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=fail_start),
        blocked_notice=notices.append,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.workspace.bot_control_widget.start_button.isEnabled()
    )

    click(window.workspace.bot_control_widget.start_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.BLOCKED)
    qtbot.waitUntil(window.setup.isVisible)
    assert notices == []


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
        targets=basket_targets(
            basket_entries("100", quantity="1", current_price="110")
        ),
        unrealized_pnl=Decimal("10"),
        entry_gate=BasketEntryGate.OPEN,
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


def empty_chart_snapshot(
    session: ConfiguredPaperSession,
    chart_range: ChartRange,
) -> ChartSnapshot:
    return ChartSnapshot(
        session=session,
        chart_range=chart_range,
        observed_at_utc=chart_range.end,
        candles=(),
        fills=(),
        state=ChartReadState.EMPTY,
    )


def test_the_chart_reloads_the_candles_a_dropped_feed_swallowed(
    qtbot: QtBot,
) -> None:
    """The connection the chart lives on before any Bot is started.

    A feed that drops and comes back leaves a hole: the candles of the
    outage were never streamed, so the chart has to go and read them.
    """
    session = configured_spot_session()
    relay = ChartFactRelay()
    started_feeds: list[ConfiguredPaperSession] = []
    loads: list[ChartRange] = []

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        loads.append(chart_range)
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            facts=relay,
            load=load_chart,
            start_feed=started_feeds.append,
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: len(loads) == 1)
    assert started_feeds == [session]

    publisher = relay.new_generation()
    publisher.feed_state(ChartFeedState.RECONNECTING)

    qtbot.waitUntil(lambda: window._chart_feed_state is ChartFeedState.RECONNECTING)

    publisher.feed_state(ChartFeedState.LIVE)

    # The candles the outage swallowed are only in the history now.
    qtbot.waitUntil(lambda: len(loads) == 2)


def test_the_system_notice_says_the_worst_of_what_is_wrong(qtbot: QtBot) -> None:
    """One line, and the reader should meet the fault that matters most.

    A Bot that stopped outranks a chart feed that stopped. Reconnecting is
    not on this line at all any more — waiting on a connection is told in a
    dialog, so the line is left to the faults the reader has to act on.
    """
    session = configured_spot_session()
    relay = ChartFactRelay()
    runtime_snapshots = RuntimeSnapshotRelay()

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start, runtime_snapshots=runtime_snapshots),
        chart=ChartSources(facts=relay, load=load_chart, start_feed=lambda _s: None),
        blocked_notice=lambda _reason: None,
    )
    qtbot.addWidget(window)
    window.show()
    notice = window.workspace.chart.system_notice
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert notice.text() == ""

    chart_feed = relay.new_generation()
    runtime_facts = runtime_snapshots.new_generation()
    chart_feed.feed_state(ChartFeedState.RECONNECTING)
    runtime_facts(
        BotLifecycleResult(
            workspace=stale_workspace_snapshot(
                window._lifecycle_workflow.snapshot.workspace
            )
        )
    )
    qtbot.waitUntil(lambda: window._bot_reconnecting)
    assert notice.text() == ""

    chart_feed.feed_state(ChartFeedState.STOPPED)
    qtbot.waitUntil(lambda: notice.text() == CHART_FEED_STOPPED_NOTICE)

    runtime_facts(
        BotLifecycleResult(
            workspace=paper_runtime_blocked_workspace_snapshot(
                window._lifecycle_workflow.snapshot.workspace
            ),
            blocked_reason="Paper Bot stopped: the Session could not continue",
        )
    )
    qtbot.waitUntil(
        lambda: notice.text() == "Paper Bot stopped: the Session could not continue"
    )


def test_the_system_notice_clears_when_the_chart_feed_comes_back(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    relay = ChartFactRelay()

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(facts=relay, load=load_chart, start_feed=lambda _s: None),
    )
    qtbot.addWidget(window)
    window.show()
    notice = window.workspace.chart.system_notice
    publisher = relay.new_generation()
    publisher.feed_state(ChartFeedState.STOPPED)
    qtbot.waitUntil(lambda: notice.text() == CHART_FEED_STOPPED_NOTICE)

    publisher.feed_state(ChartFeedState.LIVE)

    qtbot.waitUntil(lambda: notice.text() == "")


class _RecordingConnectionNotice:
    """Stands in for the dialog, so a test reads what was said, not clicked."""

    def __init__(self) -> None:
        self.shown = 0
        self.hidden = 0

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1


def _window_watching_a_chart_feed(
    qtbot: QtBot,
    notice: _RecordingConnectionNotice,
    *,
    delay_ms: int,
) -> tuple[MainWindow, ChartFactRelay]:
    session = configured_spot_session()
    relay = ChartFactRelay()

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(facts=relay, load=load_chart, start_feed=lambda _s: None),
        connection_notice=notice,
        connection_notice_delay_ms=delay_ms,
    )
    qtbot.addWidget(window)
    window.show()
    return window, relay


def test_a_connection_that_comes_back_quickly_is_never_announced(
    qtbot: QtBot,
) -> None:
    """The runtime retries forever, so most drops are a blink.

    Announcing every one of them would put a dialog over the chart each time
    a phone hotspot wavered, and the trader would learn to dismiss it.
    """
    notice = _RecordingConnectionNotice()
    window, relay = _window_watching_a_chart_feed(qtbot, notice, delay_ms=60_000)
    publisher = relay.new_generation()

    publisher.feed_state(ChartFeedState.RECONNECTING)
    publisher.feed_state(ChartFeedState.LIVE)

    qtbot.waitUntil(lambda: not window._connection_notice_timer.isActive())
    assert notice.shown == 0


def test_a_connection_still_away_after_the_wait_is_announced(qtbot: QtBot) -> None:
    notice = _RecordingConnectionNotice()
    _window, relay = _window_watching_a_chart_feed(qtbot, notice, delay_ms=0)

    relay.new_generation().feed_state(ChartFeedState.RECONNECTING)

    qtbot.waitUntil(lambda: notice.shown == 1)


def test_more_reconnect_news_neither_delays_nor_repeats_the_announcement(
    qtbot: QtBot,
) -> None:
    """Restarting the countdown on every update would defer it forever.

    A drop keeps producing snapshots the whole time it lasts, so a timer
    that started again on each one would never reach the end of its wait.
    """
    notice = _RecordingConnectionNotice()
    window, relay = _window_watching_a_chart_feed(qtbot, notice, delay_ms=60_000)
    publisher = relay.new_generation()
    publisher.feed_state(ChartFeedState.RECONNECTING)
    qtbot.waitUntil(window._connection_notice_timer.isActive)
    remaining = window._connection_notice_timer.remainingTime()
    qtbot.wait(20)

    publisher.feed_state(ChartFeedState.RECONNECTING)
    publisher.feed_state(ChartFeedState.RECONNECTING)

    assert window._connection_notice_timer.remainingTime() < remaining
    assert notice.shown == 0


def test_the_announcement_is_taken_back_when_prices_are_current_again(
    qtbot: QtBot,
) -> None:
    notice = _RecordingConnectionNotice()
    _window, relay = _window_watching_a_chart_feed(qtbot, notice, delay_ms=0)
    publisher = relay.new_generation()
    publisher.feed_state(ChartFeedState.RECONNECTING)
    qtbot.waitUntil(lambda: notice.shown == 1)

    publisher.feed_state(ChartFeedState.LIVE)

    qtbot.waitUntil(lambda: notice.hidden == 1)


def test_closing_the_window_takes_the_announcement_down_with_it(
    qtbot: QtBot,
) -> None:
    notice = _RecordingConnectionNotice()
    window, relay = _window_watching_a_chart_feed(qtbot, notice, delay_ms=0)
    relay.new_generation().feed_state(ChartFeedState.RECONNECTING)
    qtbot.waitUntil(lambda: notice.shown == 1)

    window.close()

    qtbot.waitUntil(lambda: notice.hidden == 1)
    assert not window._connection_notice_timer.isActive()


def test_a_bot_that_stops_replaces_the_connection_notice_with_its_own(
    qtbot: QtBot,
) -> None:
    """Two dialogs over one window say two different things about one fault.

    A Bot that stopped is not waiting on anything, so the notice that says
    it is has to go before the one that says the Bot is gone arrives — even
    while the chart, which is a second connection, is still reaching for its
    own feed and would otherwise keep the notice up.
    """
    session = configured_spot_session()
    relay = ChartFactRelay()
    runtime_snapshots = RuntimeSnapshotRelay()
    notice = _RecordingConnectionNotice()
    blocked: list[str] = []

    def start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start, runtime_snapshots=runtime_snapshots),
        chart=ChartSources(facts=relay, load=load_chart, start_feed=lambda _s: None),
        blocked_notice=blocked.append,
        connection_notice=notice,
        connection_notice_delay_ms=0,
    )
    qtbot.addWidget(window)
    window.show()
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)
    click(control.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    relay.new_generation().feed_state(ChartFeedState.RECONNECTING)
    runtime_facts = runtime_snapshots.new_generation()
    runtime_facts(
        BotLifecycleResult(
            workspace=stale_workspace_snapshot(
                window._lifecycle_workflow.snapshot.workspace
            )
        )
    )
    qtbot.waitUntil(lambda: notice.shown == 1)

    runtime_facts(
        BotLifecycleResult(
            workspace=paper_runtime_blocked_workspace_snapshot(
                window._lifecycle_workflow.snapshot.workspace
            ),
            blocked_reason="Paper Bot stopped: market data could not be recovered",
        )
    )

    qtbot.waitUntil(lambda: notice.hidden == 1)
    qtbot.waitUntil(
        lambda: blocked == ["Paper Bot stopped: market data could not be recovered"]
    )


def test_retrying_a_stopped_chart_feed_starts_a_new_one(qtbot: QtBot) -> None:
    """Reloading history off a feed that is gone leaves the chart just as dead."""
    session = configured_spot_session()
    relay = ChartFactRelay()
    started_feeds: list[ConfiguredPaperSession] = []
    loads: list[ChartRange] = []

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        loads.append(chart_range)
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            facts=relay,
            load=load_chart,
            start_feed=started_feeds.append,
        ),
    )
    qtbot.addWidget(window)
    window.show()
    chart = window.workspace.chart
    qtbot.waitUntil(lambda: len(loads) == 1)

    relay.new_generation().feed_state(ChartFeedState.STOPPED)

    qtbot.waitUntil(chart.retry_button.isVisible)
    click(chart.retry_button)

    qtbot.waitUntil(lambda: started_feeds == [session, session])
    qtbot.waitUntil(lambda: len(loads) == 2)


def test_a_feed_state_from_a_replaced_chart_feed_is_ignored(qtbot: QtBot) -> None:
    session = configured_spot_session()
    relay = ChartFactRelay()

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        return empty_chart_snapshot(loaded, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(facts=relay, load=load_chart, start_feed=lambda _s: None),
    )
    qtbot.addWidget(window)
    window.show()
    replaced = relay.new_generation()
    relay.new_generation()

    replaced.feed_state(ChartFeedState.RECONNECTING)

    qtbot.wait(10)
    assert window.workspace.chart.system_notice.text() == ""


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
                    group_id=1,
                ),
            ),
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
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
    qtbot.waitUntil(lambda: _marked_rows(window) == 1)
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


def _record_close_entry(
    sold: list[tuple[str, int]],
) -> Callable[[ConfiguredPaperSession, int], bool]:
    """Take the ask the way the desktop takes it: the Pair, and which Entry."""

    def close_entry(session: ConfiguredPaperSession, entry_number: int) -> bool:
        sold.append((session.market_data.symbol, entry_number))
        return True

    return close_entry


def _close_position_fake(
    replaceable: bool,
    closes: list[str],
    closed_all: list[str] | None,
) -> Callable[[ConfiguredPaperSession], None] | None:
    """Wired for the replace-session flow, the Close All button, or neither."""
    if not replaceable and closed_all is None:
        return None

    def close_position(_session: ConfiguredPaperSession) -> None:
        if replaceable:
            closes.append("closed")
        if closed_all is not None:
            closed_all.append("closed")

    return close_position


def _open_position_window(
    qtbot: QtBot,
    *,
    started: list[BotControlSnapshot],
    asked: list[tuple[str, Callable[[], None]]],
    price: int | None,
    replaceable: bool = False,
    closed: list[str] | None = None,
    second_entry_price: int | None = None,
    closed_entries: list[tuple[str, int]] | None = None,
    closed_all: list[str] | None = None,
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
            group_id=1,
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
                group_id=1,
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
            close_position=_close_position_fake(replaceable, closes, closed_all),
            close_entry=(
                None if closed_entries is None else _record_close_entry(closed_entries)
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
    # The rows are there as soon as the Basket is read, so their arrival says
    # nothing about the candle. What the candle adds is the marking, which is
    # what a test asking about a price has to wait for.
    qtbot.waitUntil(lambda: _marked_rows(window) == len(entries))
    return window


def _marked_rows(window: MainWindow) -> int:
    """How many rows have been marked against a price."""
    table = window.workspace.position_basket
    column = table.headers.index("Unrealized P/L")
    marked = 0
    for row in range(table.table.rowCount()):
        item = table.table.item(row, column)
        if item is not None and item.text() != BLANK:
            marked += 1
    return marked


def test_an_open_position_is_on_the_tab_before_any_price_arrives(
    qtbot: QtBot,
) -> None:
    """The window opens on a Position, and the chart says a price seconds later.

    In between, the tab used to read "Loading Position…" over an empty table —
    so a trader who pressed Start met a question about an Entry the tab had
    never shown, and read the question as the mistake.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=None)
    table = window.workspace.position_basket

    qtbot.waitUntil(lambda: table.table.rowCount() == 1)
    headings = table.headers
    entry_price = table.table.item(0, headings.index("Entry Price"))
    pnl = table.table.item(0, headings.index("Unrealized P/L"))
    assert entry_price is not None and pnl is not None
    assert "100" in entry_price.text()
    # The one column a price answers, and no price has arrived.
    assert pnl.text() == BLANK
    assert table.empty_state.isHidden()


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

    qtbot.waitUntil(lambda: len(asked) == 1)
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

    qtbot.waitUntil(lambda: len(asked) == 1)
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

    qtbot.waitUntil(lambda: len(asked) == 1)
    message, confirm = asked[0]
    # That the sale books at the target rather than at the price on screen —
    # stated, not priced: the figures are columns in the tab behind it.
    assert message.startswith("The Position is already at its Take Profit.")
    assert "at once at that target, not at the price now" in message
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

    qtbot.waitUntil(lambda: len(asked) == 1)
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

    qtbot.waitUntil(lambda: len(asked) == 1)
    message, confirm = asked[0]
    # What is held, not what a sale would book: this one has reached nothing.
    assert message == "1 Entry is open."
    assert started == []

    confirm()

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(started) == 1


def test_a_row_sells_its_own_entry_once_the_trader_says_so(qtbot: QtBot) -> None:
    """A Fill has no way back, and the row redraws under the hand every tick.

    So the Entry is named in the question, and the answer that trades nothing
    is the one a stray key finds.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    sold: list[tuple[str, int]] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=105,
        closed_entries=sold,
    )
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Market", seen))
    _click_close_entry(window, 0)

    qtbot.waitUntil(lambda: sold == [("BTCUSDT", 1)])
    assert len(seen) == 1
    assert "Entry 1" in seen[0].text()
    assert seen[0].defaultButton() is not None
    assert seen[0].defaultButton().text() == "Cancel"


def test_cancelling_the_sale_of_an_entry_leaves_it_where_it_is(
    qtbot: QtBot,
) -> None:
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    sold: list[tuple[str, int]] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=105,
        closed_entries=sold,
    )
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    _click_close_entry(window, 0)

    assert len(seen) == 1
    assert sold == []
    assert window._open_position.basket is not None


def _click_close_entry(window: MainWindow, row: int) -> None:
    button = window.workspace.position_basket.close_button(row)
    assert isinstance(button, QPushButton)
    click(button)


def _click_close_all(window: MainWindow) -> None:
    click(window.workspace.position_basket.close_all_button)


def test_close_all_sells_the_whole_position_once_the_trader_says_so(
    qtbot: QtBot,
) -> None:
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed_all: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=105,
        closed_all=closed_all,
    )
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Close All", seen))
    _click_close_all(window)

    qtbot.waitUntil(lambda: closed_all == ["closed"])
    assert len(seen) == 1
    assert seen[0].defaultButton() is not None
    assert seen[0].defaultButton().text() == "Cancel"


def test_cancelling_close_all_leaves_the_position_where_it_is(
    qtbot: QtBot,
) -> None:
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed_all: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=105,
        closed_all=closed_all,
    )
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    _click_close_all(window)

    assert len(seen) == 1
    assert closed_all == []
    assert window._open_position.basket is not None


def _click_settings(window: MainWindow) -> None:
    click(window.workspace.settings_button)


def test_settings_button_shows_the_panel_in_place_of_the_bot_control_card(
    qtbot: QtBot,
) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        clear_database=lambda: None,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    _click_settings(window)

    assert window.workspace.is_showing_settings
    assert window.workspace.settings_panel.isVisible()


def test_settings_button_toggles_back_to_the_page_it_replaced(qtbot: QtBot) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        clear_database=lambda: None,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    _click_settings(window)
    assert window.workspace.is_showing_settings

    _click_settings(window)
    assert not window.workspace.is_showing_settings
    assert window.setup.isVisible()


def test_settings_back_button_returns_to_the_page_it_replaced(qtbot: QtBot) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        clear_database=lambda: None,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    _click_settings(window)
    click(window.workspace.settings_panel.back_button)

    assert not window.workspace.is_showing_settings
    assert window.setup.isVisible()


def test_clicking_clear_database_while_running_warns_instead_of_confirming(
    qtbot: QtBot,
) -> None:
    """A running Bot must not even reach the destructive confirmation."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    window._clear_database = lambda: pytest.fail("clear_database must not run")

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
    _click_settings(window)

    seen: list[str] = []

    def close_warning() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        seen.append(dialog.text())
        dialog.close()

    QTimer.singleShot(0, close_warning)
    click(window.workspace.settings_panel.clear_database_button)

    assert seen == ["Stop the bot before clearing the database."]
    assert window.workspace.is_showing_settings


def test_clicking_clear_database_after_stopping_proceeds_to_the_confirmation(
    qtbot: QtBot,
) -> None:
    """Stopping the Bot must hand the action back, not leave it stuck off.

    Stopping the Bot always lands back on the editable Setup form, whose only
    way onward retires the Session and creates a fresh one — so this state is
    exactly as safe to clear from as no Session at all.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    cleared: list[str] = []
    window._clear_database = lambda: cleared.append("cleared")

    running = replace(
        window._lifecycle_workflow.snapshot,
        state=BotRuntimeState.RUNNING,
        available_actions=frozenset({BotControlAction.STOP}),
        workspace=workspace_with_runtime_state(
            window._lifecycle_workflow.snapshot.workspace,
            BotRuntimeState.RUNNING,
            data_freshness=DataFreshness.FRESH,
        ),
    )
    window._bot_control_changed(running)
    window._bot_control_changed(
        replace(
            running,
            state=BotRuntimeState.STOPPED,
            available_actions=frozenset({BotControlAction.START}),
            workspace=workspace_with_runtime_state(
                running.workspace,
                BotRuntimeState.STOPPED,
                data_freshness=DataFreshness.FRESH,
            ),
        )
    )
    _click_settings(window)

    def confirm_clear() -> None:
        confirm_dialog = QApplication.activeModalWidget()
        assert isinstance(confirm_dialog, QMessageBox)
        click(next(b for b in confirm_dialog.buttons() if b.text() == "Clear All"))

    QTimer.singleShot(0, confirm_clear)
    click(window.workspace.settings_panel.clear_database_button)

    qtbot.waitUntil(lambda: cleared == ["cleared"])


def test_clearing_the_database_refreshes_the_position_tab(qtbot: QtBot) -> None:
    """A cleared database leaves no Basket for a stale Close All to find.

    Left alone, the Position tab would go on showing the Basket it read
    before the clear, with Close All still offered — and pressed, it would
    find nothing left in the database to sell, recording nothing in either
    History tab. The clear itself must read the tab back, the same way a
    real Close All already does.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot, started=started, asked=asked, price=105, closed=closed
    )
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    # The fake database's Basket disappears the same way `closes` already
    # makes `read_open_basket` answer None elsewhere in this fixture.
    window._clear_database = lambda: closed.append("cleared")
    _click_settings(window)

    def confirm_clear() -> None:
        confirm_dialog = QApplication.activeModalWidget()
        assert isinstance(confirm_dialog, QMessageBox)
        click(next(b for b in confirm_dialog.buttons() if b.text() == "Clear All"))

    QTimer.singleShot(0, confirm_clear)
    click(window.workspace.settings_panel.clear_database_button)

    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 0)
    assert window._open_position.basket is None


def test_clearing_the_database_leaves_no_session_for_the_next_start_to_replace(
    qtbot: QtBot,
) -> None:
    """A wiped database holds no Session left to retire.

    The window went on holding the Session it had read before the clear, so
    the next Start took the replacement course and asked the database to
    retire a row that had just been deleted — and told the trader the Session
    was "still running somewhere", about a Session nothing was running. What
    a clear leaves is an installation with no Session at all, which is what
    the next Start has to make.
    """
    session = configured_spot_session()
    created: list[PaperSessionSetupValues] = []
    events: list[str] = []
    # Wiped along with everything else, the way the database answers after a
    # clear: no row is left for either question to find.
    stored_session: list[ConfiguredPaperSession] = [session]

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

    window = MainWindow(
        create_session=create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(
            start=start,
            stop=stop,
            close_position=lambda _session: events.append("close"),
            has_open_position=lambda _session: bool(stored_session),
            retire_session=lambda _session: events.append("retire"),
        ),
        clear_database=stored_session.clear,
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
    _click_settings(window)

    def confirm_clear() -> None:
        confirm_dialog = QApplication.activeModalWidget()
        assert isinstance(confirm_dialog, QMessageBox)
        click(next(b for b in confirm_dialog.buttons() if b.text() == "Clear All"))

    QTimer.singleShot(0, confirm_clear)
    click(window.workspace.settings_panel.clear_database_button)
    qtbot.waitUntil(lambda: not window.workspace.is_showing_settings)

    window.setup.capital_per_entry.setText("2500")
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: len(created) == 1)
    assert events == []


def test_a_session_that_is_gone_is_not_reported_as_one_still_running(
    qtbot: QtBot,
) -> None:
    """Two reasons a retirement fails, and only one of them is a running Bot.

    Told the row it went to retire was not there, the form used to say the
    Session was still running somewhere and ask the trader to stop it — an
    instruction with nothing to carry it out on.
    """
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()

    window._replacement_failed(
        PaperSessionUnavailableError("Active Paper Session is no longer there")
    )

    assert window.setup.capital_per_entry_error.text() == (
        "The existing Paper Session is no longer in the database. "
        "Try Start Paper Bot again."
    )


def test_clearing_the_database_wipes_the_chart_badges(qtbot: QtBot) -> None:
    """The Buy/Sell badges are drawings of rows that no longer exist.

    Clearing the database deletes every fill, but the chart kept holding the
    ones it had already read — so a wiped installation went on showing badges
    for trades nothing could open, close, or list any more. The clear has to
    read the fills back, the way it already reads the Position tab back.
    """
    session = configured_spot_session()
    clock = lambda: datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # noqa: E731
    stored_fills: list[TradeFill] = [
        trade_fill(
            session_id=session.config.session_id,
            filled_at_utc=datetime(2026, 1, 1, 0, 50, tzinfo=UTC),
        )
    ]

    async def load_chart(
        loaded: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        return ChartSnapshot(
            session=loaded,
            chart_range=chart_range,
            observed_at_utc=chart_range.end,
            candles=(flat_candle(datetime(2026, 1, 1, 0, 50, tzinfo=UTC), 100),),
            fills=tuple(stored_fills),
            state=ChartReadState.READY,
        )

    async def load_fills(
        _loaded: ConfiguredPaperSession,
        _visible_range: ChartRange,
    ) -> tuple[TradeFill, ...]:
        return tuple(stored_fills)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(load=load_chart, load_fills=load_fills, clock=clock),
        clear_database=stored_fills.clear,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.workspace.chart.marker_labels != ())
    _click_settings(window)

    def confirm_clear() -> None:
        confirm_dialog = QApplication.activeModalWidget()
        assert isinstance(confirm_dialog, QMessageBox)
        click(next(b for b in confirm_dialog.buttons() if b.text() == "Clear All"))

    QTimer.singleShot(0, confirm_clear)
    click(window.workspace.settings_panel.clear_database_button)

    qtbot.waitUntil(lambda: window.workspace.chart.marker_labels == ())


def test_settings_panel_stays_up_while_a_running_bot_keeps_publishing(
    qtbot: QtBot,
) -> None:
    """A running Bot's own snapshots must not sweep Settings off screen."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    window = _open_position_window(qtbot, started=started, asked=asked, price=105)
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)

    running = replace(
        window._lifecycle_workflow.snapshot,
        state=BotRuntimeState.RUNNING,
        available_actions=frozenset({BotControlAction.STOP}),
        workspace=workspace_with_runtime_state(
            window._lifecycle_workflow.snapshot.workspace,
            BotRuntimeState.RUNNING,
            data_freshness=DataFreshness.FRESH,
        ),
    )
    window._bot_control_changed(running)
    _click_settings(window)
    assert window.workspace.is_showing_settings

    window._bot_control_changed(running)

    assert window.workspace.is_showing_settings

    click(window.workspace.settings_panel.back_button)
    assert window.workspace.bot_control_widget.isVisible()


def test_confirming_clear_database_runs_it_and_refreshes_history(
    qtbot: QtBot,
) -> None:
    """A cleared database has nothing left for the History tabs to hold."""
    cleared: list[str] = []
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        clear_database=lambda: cleared.append("cleared"),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)
    _click_settings(window)

    def confirm_clear() -> None:
        confirm_dialog = QApplication.activeModalWidget()
        assert isinstance(confirm_dialog, QMessageBox)
        click(next(b for b in confirm_dialog.buttons() if b.text() == "Clear All"))

    QTimer.singleShot(0, confirm_clear)
    click(window.workspace.settings_panel.clear_database_button)

    qtbot.waitUntil(lambda: cleared == ["cleared"])
    qtbot.waitUntil(lambda: not window.workspace.is_showing_settings)


def test_cancelling_clear_database_does_not_run_it(qtbot: QtBot) -> None:
    cleared: list[str] = []
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        clear_database=lambda: cleared.append("cleared"),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)
    _click_settings(window)

    def cancel_confirm() -> None:
        confirm_dialog = QApplication.activeModalWidget()
        assert isinstance(confirm_dialog, QMessageBox)
        click(next(b for b in confirm_dialog.buttons() if b.text() == "Cancel"))

    QTimer.singleShot(0, cancel_confirm)
    click(window.workspace.settings_panel.clear_database_button)

    assert cleared == []
    assert window.workspace.is_showing_settings


def test_the_question_is_put_to_the_position_that_is_there_now(
    qtbot: QtBot,
) -> None:
    """The copy on screen is only as new as the last time something read it.

    A Session left running elsewhere trades past it, and the window read the
    Basket when it opened with nothing asking since — so Start put a question
    about an Entry at its Take Profit while the Basket had moved on to one
    that had reached nothing. The reading is taken again before the asking.
    """
    session = configured_spot_session()
    relay = ChartFactRelay()
    reads: list[int] = []

    def read_open_basket(_session: ConfiguredPaperSession) -> ReopenedBasket | None:
        reads.append(len(reads))
        # Taken at 100 its target of 106 is behind the price of 110; taken at
        # 120 its target of 126 is still ahead of it.
        return _basket_of(session, "100" if len(reads) == 1 else "120")

    window = _position_window(
        qtbot,
        session=session,
        relay=relay,
        read_open_basket=read_open_basket,
        price=110,
    )
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: len(seen) == 1)
    # The Entry that was at its target has gone; what is held has reached
    # nothing, so running the Session on is on offer again.
    assert {button.text() for button in seen[0].buttons()} == {
        "Start Bot",
        "Close at Market",
        "Cancel",
    }
    assert len(reads) >= 2


def test_the_workspace_start_asks_about_the_position_that_is_there_now(
    qtbot: QtBot,
) -> None:
    """The other Start means the same thing to a Position, and reads it too.

    Its copy refreshes when a Session loads, when the Bot reports stopped, and
    when something forgets it — never because a question is about to be put on
    it. Asked from that copy, it warned about selling at a target belonging to
    an Entry that had gone.
    """
    session = configured_spot_session()
    relay = ChartFactRelay()
    reads: list[int] = []
    asked: list[tuple[str, Callable[[], None]]] = []

    def read_open_basket(_session: ConfiguredPaperSession) -> ReopenedBasket | None:
        reads.append(len(reads))
        # Taken at 100 its target of 106 is behind the price of 110; taken at
        # 120 its target of 126 is still ahead of it.
        return _basket_of(session, "100" if len(reads) == 1 else "120")

    window = _position_window(
        qtbot,
        session=session,
        relay=relay,
        read_open_basket=read_open_basket,
        price=110,
        take_profit_confirmation=lambda message, confirm: asked.append(
            (message, confirm)
        ),
    )
    control = window.workspace.bot_control_widget
    qtbot.waitUntil(control.start_button.isEnabled)

    click(control.start_button)

    qtbot.waitUntil(lambda: len(asked) == 1)
    # What is held now, which has reached nothing — not the sale at a target
    # the first reading would have warned about.
    assert asked[0][0] == "1 Entry is open."
    assert len(reads) >= 2


def _basket_of(session: ConfiguredPaperSession, price: str) -> ReopenedBasket:
    """One Entry taken at a price, with an ATR of 2 and a multiplier of 3."""
    return reopen_basket(
        OpenBasketState(
            basket_id=UUID("00000000-0000-0000-0000-0000000000d3"),
            entries=(
                OpenBasketEntry(
                    entry_number=1,
                    price=Decimal(price),
                    quantity=Decimal("2"),
                    filled_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
                    atr=Decimal("2"),
                    group_id=1,
                ),
            ),
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        tick_size=Decimal("0.01"),
    )


def _position_window(
    qtbot: QtBot,
    *,
    session: ConfiguredPaperSession,
    relay: ChartFactRelay,
    read_open_basket: Callable[[ConfiguredPaperSession], ReopenedBasket | None],
    price: int,
    take_profit_confirmation: Callable[[str, Callable[[], None]], None] | None = None,
) -> MainWindow:
    """A stopped Session whose Basket is read by the test, at a given price."""
    window = MainWindow(
        create_session=lambda _values: PaperSessionCreateOutcome(
            session=session, created=True
        ),
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(
            start=lambda snapshot: BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    snapshot.workspace,
                    BotRuntimeState.RUNNING,
                    data_freshness=DataFreshness.FRESH,
                )
            ),
            read_open_basket=read_open_basket,
            has_open_position=lambda _session: True,
            close_position=lambda _session: None,
            retire_session=lambda _session: None,
        ),
        chart=ChartSources(facts=relay),
        take_profit_confirmation=take_profit_confirmation,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._open_position.basket is not None)
    relay.new_generation().in_progress_candle(
        flat_candle(datetime(2026, 8, 1, 12, 5, tzinfo=UTC), price)
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    return window


def _when_asked(answer: Callable[[QMessageBox], None]) -> Callable[[], None]:
    """Do this to the dialog once it is on screen, however long that takes.

    The question waits on a reading of the Basket, so it is not up the instant
    Start is pressed. Fired into the moment before it opens, an answer clicks
    nothing and leaves the dialog waiting for one that never comes.
    """
    attempts = itertools.count()

    def waiting() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            if next(attempts) < 500:
                QTimer.singleShot(5, waiting)
            return
        assert isinstance(dialog, QMessageBox)
        answer(dialog)

    return waiting


def _answer_position_dialog(label: str, seen: list[QMessageBox]) -> Callable[[], None]:
    """Click a button of the dialog the setup form's Start puts up."""

    def answer(dialog: QMessageBox) -> None:
        seen.append(dialog)
        click(next(button for button in dialog.buttons() if button.text() == label))

    return _when_asked(answer)


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
    # The state it is in, said once however many Entries the Basket holds.
    assert dialog.informativeText().startswith(
        "The Position is already at its Take Profit."
    )
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

    qtbot.waitUntil(lambda: len(seen) == 1)
    labels = {button.text() for button in seen[0].buttons()}
    assert "Sell at Take Profit" not in labels
    assert not any("Take Profit" in label for label in labels)
    # Nothing on offer resumes the Session onto the Position it holds.
    assert started == []


def test_a_start_before_any_price_is_still_offered_a_start(qtbot: QtBot) -> None:
    """The window opens with no price, and Start has to work from there.

    Nothing can be said about what a Start would sell until a candle has
    arrived, and that silence used to be read as every Entry being at its
    target: the box came up offering to close the whole Position at the
    market price, or nothing. Pressing Start on a Position that was losing
    on every Entry had no answer that ran the Bot.
    """
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=None,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Cancel", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: len(seen) == 1)
    dialog = seen[0]
    assert {button.text() for button in dialog.buttons()} == {
        "Start Bot",
        "Close at Market",
        "Cancel",
    }
    # What is held is known without a price; what a Start would sell is not,
    # and the box says so rather than guessing either way.
    assert dialog.informativeText().startswith("1 Entry is open.")
    assert "cannot be read yet" in dialog.informativeText()
    assert dialog.defaultButton() is not None
    assert dialog.defaultButton().text() == "Cancel"
    assert closed == []
    assert started == []


def test_starting_on_a_position_no_price_has_reached_yet_resumes_the_session(
    qtbot: QtBot,
) -> None:
    """The answer that runs the Bot on runs it, and sells nothing to do it."""
    started: list[BotControlSnapshot] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    closed: list[str] = []
    window = _open_position_window(
        qtbot,
        started=started,
        asked=asked,
        price=None,
        replaceable=True,
        closed=closed,
    )
    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    seen: list[QMessageBox] = []

    QTimer.singleShot(0, _answer_position_dialog("Start Bot", seen))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert len(started) == 1
    assert closed == []


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

    qtbot.waitUntil(lambda: len(seen) == 1)
    assert {button.text() for button in seen[0].buttons()} == {
        "Close at Market",
        "Cancel",
    }
    assert (
        seen[0]
        .informativeText()
        .startswith("The Position is already at its Take Profit.")
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

    qtbot.waitUntil(lambda: len(seen) == 1)
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

    # The question waits on a reading of the Basket before it is put.
    qtbot.waitUntil(lambda: window.findChild(QMessageBox) is not None)
    dialog = window.findChild(QMessageBox)
    assert dialog is not None
    cancel = next(button for button in dialog.buttons() if button.text() == "Cancel")
    assert dialog.defaultButton() is cancel
    assert dialog.escapeButton() is cancel
    assert "The Position is already at its Take Profit." in dialog.informativeText()
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

    def close_at_market(dialog: QMessageBox) -> None:
        seen.append(dialog.informativeText())
        click(
            next(
                button
                for button in dialog.buttons()
                if button.text() == "Close at Market"
            )
        )

    window.setup.capital_per_entry.setText("2500")
    QTimer.singleShot(0, _when_asked(close_at_market))
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

    def read_and_keep(dialog: QMessageBox) -> None:
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
    QTimer.singleShot(0, _when_asked(read_and_keep))
    click(window.setup.create_button)

    qtbot.waitUntil(lambda: len(buttons) == 1)
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

    qtbot.waitUntil(lambda: len(seen) == 1)
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


def test_opening_settings_fills_the_line_form_from_what_is_stored(
    qtbot: QtBot,
) -> None:
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        read_line_notifications=lambda: (True, "U123", "token-value"),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    _click_settings(window)

    panel = window.workspace.settings_panel
    qtbot.waitUntil(lambda: panel.line_target_id.text() == "U123")
    assert panel.line_enabled.isChecked()
    assert panel.line_access_token.text() == "token-value"


def test_saving_line_settings_hands_them_off_and_reads_them_back(
    qtbot: QtBot,
) -> None:
    """What the form shows after saving is what was stored, not what was typed.

    The two are not the same thing — a token that never reached the machine
    would leave a form claiming it had — so the window hands the settings off
    and fills the form from the reading that comes back.
    """
    saved: list[tuple[bool, str, str]] = []
    readings = [(False, "", ""), (True, "U123", "stored-token")]

    def save(enabled: bool, target_id: str, access_token: str) -> None:
        saved.append((enabled, target_id, access_token))

    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        save_line_notifications=save,
        read_line_notifications=lambda: readings[min(len(saved), 1)],
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)
    _click_settings(window)
    panel = window.workspace.settings_panel
    qtbot.waitUntil(lambda: panel.save_line_button.isEnabled())

    panel.line_enabled.setChecked(True)
    panel.line_target_id.setText("U123")
    panel.line_access_token.setText("token-value")
    click(panel.save_line_button)

    qtbot.waitUntil(lambda: saved == [(True, "U123", "token-value")])
    qtbot.waitUntil(lambda: panel.line_access_token.text() == "stored-token")


def test_line_settings_that_cannot_be_stored_are_reported_where_they_were_typed(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def save(enabled: bool, target_id: str, access_token: str) -> None:
        del enabled, target_id, access_token
        raise RuntimeError("nowhere to keep them")

    seen: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, text, *args: seen.append(text),
    )
    window = MainWindow(
        create_session=unused_create,
        load_active=no_active_session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        save_line_notifications=save,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)
    _click_settings(window)
    panel = window.workspace.settings_panel

    click(panel.save_line_button)

    qtbot.waitUntil(lambda: bool(seen))
    assert "Could not save the LINE settings." in seen[0]
    qtbot.waitUntil(panel.save_line_button.isEnabled)


_LIVE_PRICE_CANDLE = flat_candle(datetime(2026, 8, 2, 9, 0, tzinfo=UTC), "78355.24")


def _window_reading_prices(qtbot: QtBot) -> tuple[MainWindow, ChartFactRelay]:
    """A window whose chart is loaded and whose feed can be spoken for."""
    session = configured_spot_session()
    relay = ChartFactRelay()
    loaded: list[ChartRange] = []

    async def load_chart(
        configured: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        loaded.append(chart_range)
        return empty_chart_snapshot(configured, chart_range)

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        chart=ChartSources(
            facts=relay,
            load=load_chart,
            select_view=lambda _session, _selection: None,
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: bool(loaded))
    return window, relay


def test_the_price_card_names_the_market_before_a_price_has_arrived(
    qtbot: QtBot,
) -> None:
    window, _relay = _window_reading_prices(qtbot)

    assert window.workspace.live_price_card.pair.text() == "BTCUSDT · 5m · Spot"
    assert window.workspace.live_price_card.price.text() == BLANK


def test_a_price_the_chart_view_has_no_room_for_still_reaches_the_card(
    qtbot: QtBot,
) -> None:
    """The card is the answer to a chart that has been panned off the present.

    The chart draws the forming candle only at the end of the range it holds,
    so a price arriving for any other candle is dropped on the way to it. The
    price is still the price, and the card takes it from the feed itself
    rather than from what the chart made of it.
    """
    window, relay = _window_reading_prices(qtbot)
    card = window.workspace.live_price_card

    relay.new_generation().in_progress_candle(_LIVE_PRICE_CANDLE)

    qtbot.waitUntil(lambda: card.price.text() == "78,355.24")
    # The chart itself never took it: the workflow drops a price that does
    # not belong to the candle at the end of the range it is holding.
    assert window._chart_workflow._snapshot is not None
    assert window._chart_workflow._snapshot.in_progress_candle is None


def test_a_price_from_a_replaced_feed_never_reaches_the_card(qtbot: QtBot) -> None:
    window, relay = _window_reading_prices(qtbot)
    card = window.workspace.live_price_card
    replaced = relay.new_generation()
    relay.new_generation()

    replaced.in_progress_candle(_LIVE_PRICE_CANDLE)

    qtbot.wait(50)
    assert card.price.text() == BLANK


def test_the_price_card_dims_while_the_feed_is_away(qtbot: QtBot) -> None:
    window, relay = _window_reading_prices(qtbot)
    card = window.workspace.live_price_card
    publisher = relay.new_generation()
    publisher.in_progress_candle(_LIVE_PRICE_CANDLE)
    qtbot.waitUntil(lambda: card.price.text() == "78,355.24")

    publisher.feed_state(ChartFeedState.RECONNECTING)

    qtbot.waitUntil(lambda: card.is_stale)
    # Dimmed, not blanked: the last price is still the last thing known.
    assert card.price.text() == "78,355.24"

    publisher.feed_state(ChartFeedState.LIVE)

    qtbot.waitUntil(lambda: not card.is_stale)


def test_choosing_another_pair_takes_the_old_price_off_the_card(
    qtbot: QtBot,
) -> None:
    window, relay = _window_reading_prices(qtbot)
    card = window.workspace.live_price_card
    relay.new_generation().in_progress_candle(_LIVE_PRICE_CANDLE)
    qtbot.waitUntil(lambda: card.price.text() == "78,355.24")

    window.workspace.chart.symbol.setCurrentIndex(
        window.workspace.chart.symbol.findData("ETHUSDT")
    )

    qtbot.waitUntil(lambda: card.pair.text() == "ETHUSDT · 5m · Spot")
    assert card.price.text() == BLANK
