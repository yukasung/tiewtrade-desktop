import ast
import asyncio
import socket
import sqlite3
from collections.abc import AsyncIterator, Awaitable
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import TypeVar
from uuid import UUID

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click, table_item
from tests.support.trade_history_ui import (
    empty_basket_history,
    empty_trade_fills,
)
from tests.support.workspace_snapshots import basket_entries, basket_targets
from tiewtrade.application.bot_control import (
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.paper_runtime import PaperRuntimeController
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
)
from tiewtrade.application.trade_history import TradeHistoryFilter
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    TradingWorkspaceSnapshot,
    WorkspaceReadState,
    configured_workspace_snapshot,
    ready_position_basket_tab,
)
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataKlineUpdate
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.trading.cooldown_policy import CooldownPolicy
from tiewtrade.trading.futures_policy import MarginMode, PositionMode
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import BasketStatus
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.main_window import BotActions, MainWindow
from tiewtrade.ui.position_basket_table import PositionBasketTable
from tiewtrade.ui.trading_workspace import WorkspaceTab

_T = TypeVar("_T")
_RUNTIME_NOW = datetime(2026, 8, 2, 3, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _SessionCase:
    market_type: str
    timeframe: str
    max_entries: int
    futures_leverage: int | None
    cooldown: CooldownPolicy


class _ImmediateRuntimeScheduler:
    def now(self) -> datetime:
        return _RUNTIME_NOW

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T:
        return await awaitable


class _AcceptancePublicCandleSource:
    def __init__(
        self,
        *,
        config: MarketDataConfig,
        warm_up: tuple[Candle, ...],
        live: tuple[Candle, ...],
    ) -> None:
        self._config = config
        self._warm_up = warm_up
        self._live = tuple(
            MarketDataKlineUpdate(candle=candle, is_closed=True) for candle in live
        )
        self.load_recent_calls = 0
        self.stream_klines_calls = 0
        self.closed = Event()

    async def load_recent(
        self,
        config: MarketDataConfig,
        *,
        count: int,
        completed_before: datetime,
    ) -> tuple[Candle, ...]:
        assert config == self._config
        assert count == len(self._warm_up)
        assert completed_before == _RUNTIME_NOW
        self.load_recent_calls += 1
        return self._warm_up

    async def load_range(
        self,
        config: MarketDataConfig,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        raise AssertionError("contiguous acceptance candles must not backfill")

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        assert config == self._config
        self.stream_klines_calls += 1
        return self._stream_klines()

    async def close(self) -> None:
        self.closed.set()

    async def _stream_klines(self) -> AsyncIterator[MarketDataKlineUpdate]:
        for update in self._live:
            yield update
        await asyncio.Event().wait()


# Spot only: Binance TH lists no Futures market, so a Futures Session has no
# feed to start on and never reaches a running runtime. That the Bot says so
# instead of starting is covered in tests/unit/application/test_paper_runtime.
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SessionCase("spot", "5m", 10, None, CooldownPolicy.until_cancelled()),
            id="spot",
        ),
    ],
)
def test_desktop_real_paper_runtime_starts_once_and_stops_without_closing_basket(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    case: _SessionCase,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    active_sessions = SQLiteActivePaperSessions(database)
    create_session = CreatePaperSession(create_active=active_sessions.create)
    history = SQLiteTradeHistory(database)
    sources: list[_AcceptancePublicCandleSource] = []
    selected_endpoints: list[BinancePublicEndpoints] = []
    runtime_relay = RuntimeSnapshotRelay()
    controller: PaperRuntimeController | None = None
    initialize_calls = 0
    start_calls = 0

    def initialize_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        nonlocal initialize_calls
        initialize_calls += 1
        startup_controller = PaperRuntimeController(
            lifecycle=SQLitePaperRuntimeLifecycle(database),
            trade_history=history,
            symbol_rules=_acceptance_symbol_rules(snapshot.session.market_data.symbol),
            source_factory=lambda _endpoints: pytest.fail(
                "clean startup inspection must not contact public market data"
            ),
            snapshot_callback=lambda _snapshot: None,
            clock=lambda: _RUNTIME_NOW,
        )
        return _paper_runtime_result_for_ui(
            snapshot,
            startup_controller.inspect_startup(snapshot.session),
        )

    def start_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        nonlocal controller, start_calls
        start_calls += 1
        assert controller is None
        warm_up, live = _paper_runtime_candles(snapshot.session.market_data)
        source = _AcceptancePublicCandleSource(
            config=snapshot.session.market_data,
            warm_up=warm_up,
            live=live,
        )
        sources.append(source)
        publish = runtime_relay.new_generation()

        def publish_current_result(workspace: TradingWorkspaceSnapshot) -> None:
            del workspace
            assert controller is not None
            publish(controller.current_result)

        def select_source(
            endpoints: BinancePublicEndpoints,
        ) -> _AcceptancePublicCandleSource:
            selected_endpoints.append(endpoints)
            return source

        controller = PaperRuntimeController(
            lifecycle=SQLitePaperRuntimeLifecycle(database),
            trade_history=history,
            symbol_rules=_acceptance_symbol_rules(snapshot.session.market_data.symbol),
            source_factory=select_source,
            snapshot_callback=publish_current_result,
            scheduler=_ImmediateRuntimeScheduler(),
            clock=lambda: _RUNTIME_NOW,
        )
        return _paper_runtime_result_for_ui(
            snapshot,
            controller.start(snapshot.session),
        )

    def stop_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        assert controller is not None
        return _paper_runtime_result_for_ui(
            snapshot,
            controller.stop(snapshot.session),
        )

    window = MainWindow(
        create_session=create_session.execute,
        load_active=active_sessions.get_active,
        list_basket_history=history.list_basket_history,
        list_trade_fills=history.list_trade_fills,
        bot=BotActions(
            start=start_bot,
            stop=stop_bot,
            initialize=initialize_bot,
            runtime_snapshots=runtime_relay,
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)
    assert active_sessions.get_active() is None
    assert initialize_calls == 0
    assert start_calls == 0

    _enter_form_values(window, case)
    click(window.setup.create_button)

    qtbot.waitUntil(window.setup.isVisible)
    qtbot.waitUntil(lambda: start_calls == 1)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    session = active_sessions.get_active()
    assert session is not None
    assert initialize_calls == 0
    qtbot.waitUntil(lambda: window.workspace.position_basket.table.rowCount() == 1)
    assert len(sources) == 1
    source = sources[0]
    assert start_calls == 1
    assert source.load_recent_calls == 1
    assert source.stream_klines_calls == 1
    assert selected_endpoints == [
        BinancePublicEndpoints.for_market_type(MarketType(case.market_type))
    ]
    assert controller is not None
    running_workspace = controller.current_workspace
    assert running_workspace.basket is not None
    assert running_workspace.basket.market_type == case.market_type
    assert running_workspace.basket.entry_count == 1
    assert running_workspace.basket.take_profit_price is not None
    with pytest.raises(FrozenInstanceError):
        running_workspace.read_state = WorkspaceReadState.EMPTY  # type: ignore[misc]

    click(window.workspace.bot_control_widget.stop_button)
    confirmation = window.findChild(QMessageBox)
    assert confirmation is not None
    confirm_button = next(
        button for button in confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(confirm_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.STOPPED)

    stopped_workspace = controller.current_workspace
    assert source.closed.wait(1)
    assert stopped_workspace.basket == running_workspace.basket
    assert stopped_workspace.basket is not None
    assert (
        stopped_workspace.basket.take_profit_price
        == running_workspace.basket.take_profit_price
    )
    history_page = history.list_basket_history(
        TradeHistoryFilter(market_type=MarketType(case.market_type)),
    )
    assert len(history_page.items) == 1
    assert history_page.items[0].status is BasketStatus.OPEN


def _runtime_state(window: MainWindow) -> BotRuntimeState | None:
    """The state the lifecycle reached, or None before a session exists."""
    snapshot = window._lifecycle_workflow._snapshot
    return None if snapshot is None else snapshot.state


def test_desktop_restart_blocked_runtime_reopens_setup_form(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    active_sessions = SQLiteActivePaperSessions(database)
    session = (
        CreatePaperSession(create_active=active_sessions.create)
        .execute(
            _setup_values(
                _SessionCase("spot", "5m", 10, None, CooldownPolicy.until_cancelled())
            )
        )
        .session
    )
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_running(session.config.session_id, _RUNTIME_NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_acceptance_symbol_rules(session.market_data.symbol),
        source_factory=lambda _endpoints: pytest.fail(
            "startup recovery must not contact public market data"
        ),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _RUNTIME_NOW,
    )
    initialize_calls = 0

    def initialize_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        nonlocal initialize_calls
        initialize_calls += 1
        result = _paper_runtime_result_for_ui(
            snapshot,
            controller.inspect_startup(snapshot.session),
        )
        assert result.blocked_reason == "Paper Bot recovery required"
        return result

    window = MainWindow(
        create_session=lambda values: pytest.fail("create must not run"),
        load_active=active_sessions.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(initialize=initialize_bot),
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.BLOCKED)
    assert initialize_calls == 1
    qtbot.waitUntil(window.setup.isVisible)
    assert window.setup.capital_per_entry.isEnabled()
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING

    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )
    assert _runtime_state(window) is BotRuntimeState.BLOCKED
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING
    assert window.workspace.tab_pages.currentWidget() is window.workspace.trade_history
    assert window.workspace.position_basket.table.rowCount() == 0


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SessionCase("spot", "15m", 12, None, CooldownPolicy.until_cancelled()),
            id="spot",
        ),
        pytest.param(
            _SessionCase("futures", "1h", 14, 4, CooldownPolicy.until_cancelled()),
            id="futures",
        ),
    ],
)
def test_desktop_fake_snapshots_render_orders_and_basket_without_manual_trading(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    case: _SessionCase,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = (
        CreatePaperSession(create_active=store.create)
        .execute(_setup_values(case))
        .session
    )
    initial_counts = _trade_side_effect_counts(database)
    assert initial_counts == (0, 0)
    start_calls = 0

    def fake_start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        nonlocal start_calls
        start_calls += 1
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    window = MainWindow(
        create_session=lambda values: pytest.fail("create must not run"),
        load_active=store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=fake_start),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)

    window.workspace.show_workspace_snapshot(
        _fake_running_workspace(session, observed_at=datetime(2026, 8, 2, tzinfo=UTC))
    )

    assert start_calls == 0
    # One row per Entry taken, each with the price it paid and its own target.
    assert window.workspace.position_basket.table.rowCount() == 1
    lifecycle = PositionBasketTable.HEADERS.index("Status")
    assert table_item(window.workspace.position_basket.table, 0, lifecycle).text() == (
        "Active"
    )
    target = PositionBasketTable.HEADERS.index("Take Profit")
    assert table_item(window.workspace.position_basket.table, 0, target).text() != ""
    assert _manual_order_buttons(window) == ()
    assert _trade_side_effect_counts(database) == initial_counts


def test_spot_partial_fills_keep_one_order_and_basket_entry_across_tabs(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    active_sessions = SQLiteActivePaperSessions(database)
    session = (
        CreatePaperSession(create_active=active_sessions.create)
        .execute(
            _setup_values(
                _SessionCase("spot", "5m", 10, None, CooldownPolicy.until_cancelled())
            )
        )
        .session
    )
    store = SQLiteTradeHistory(database)
    history = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        store,
    )
    observed_at = datetime(2026, 8, 2, tzinfo=UTC)
    fills = (
        PaperSpotEntryFill(
            intent_id="intent-1",
            order_id="entry-order-1",
            fill_id="fill-1",
            price=Decimal("66000.1250"),
            quantity=Decimal("0.00100000"),
            filled_at=observed_at,
        ),
        PaperSpotEntryFill(
            intent_id="intent-1",
            order_id="entry-order-1",
            fill_id="fill-2",
            price=Decimal("66000.1250"),
            quantity=Decimal("0.00050000"),
            filled_at=observed_at + timedelta(seconds=1),
        ),
    )
    for fill in fills:
        assert history.record_entry(
            basket_id=UUID("00000000-0000-0000-0000-000000000135"),
            entry_number=1,
            fill=fill,
            entry_atr=Decimal("12.5"),
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        )

    basket_id = UUID("00000000-0000-0000-0000-000000000135")
    basket = store.get_basket(basket_id)
    durable_fills = store.list_fills(basket_id)
    assert basket is not None
    assert basket.entry_count == 1
    assert len(durable_fills) == 2
    assert {fill.order_id for fill in durable_fills} == {"entry-order-1"}
    initial_counts = _trade_side_effect_counts(database)
    assert initial_counts == (1, 2)

    window = MainWindow(
        create_session=lambda values: pytest.fail("create must not run"),
        load_active=active_sessions.get_active,
        list_basket_history=store.list_basket_history,
        list_trade_fills=store.list_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)
    window.workspace.show_workspace_snapshot(
        _fake_running_workspace(session, observed_at=observed_at)
    )

    # Two Fills of the same Entry stay one row: a Partial Fill is not an Entry.
    assert window.workspace.position_basket.table.rowCount() == 1
    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    # The page opens on today; this fixture is dated, so widen to reach it.
    qtbot.waitUntil(window.trade_history.basket_filters.search_button.isEnabled)
    window.trade_history.basket_filters.from_date.clear_date()
    window.trade_history.basket_filters.to_date.clear_date()
    click(window.trade_history.basket_filters.search_button)
    qtbot.waitUntil(lambda: window.trade_history.basket_table.rowCount() == 1)
    # Both Fills are still recorded; the Trade History tab is where they read.
    window.workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    # The Trades half carries its own range and opens on today as well.
    qtbot.waitUntil(window.trade_history.trade_filters.search_button.isEnabled)
    window.trade_history.trade_filters.from_date.clear_date()
    window.trade_history.trade_filters.to_date.clear_date()
    click(window.trade_history.trade_filters.search_button)
    qtbot.waitUntil(lambda: window.trade_history.fill_table.rowCount() == 2)
    assert _trade_side_effect_counts(database) == initial_counts


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SessionCase(
                market_type="spot",
                timeframe="15m",
                max_entries=12,
                futures_leverage=None,
                cooldown=CooldownPolicy.for_days(3),
            ),
            id="spot",
        ),
        pytest.param(
            _SessionCase(
                market_type="futures",
                timeframe="1h",
                max_entries=14,
                futures_leverage=4,
                cooldown=CooldownPolicy.off(),
            ),
            id="futures",
        ),
    ],
)
def test_desktop_paper_session_create_overview_and_restart_restore(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    case: _SessionCase,
) -> None:
    _block_network(monkeypatch)
    database_path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    first_store = SQLiteActivePaperSessions(database)
    initial_side_effect_counts = _trade_side_effect_counts(database)
    assert initial_side_effect_counts == (0, 0)
    expected_session_id = UUID("00000000-0000-0000-0000-000000000115")
    expected_created_at = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
    create_use_case = CreatePaperSession(
        create_active=first_store.create,
        session_ids=lambda: expected_session_id,
        clock=lambda: expected_created_at,
    )
    create_calls = 0

    def create_from_form(values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        nonlocal create_calls
        create_calls += 1
        return create_use_case.execute(values)

    first_window = MainWindow(
        create_session=create_from_form,
        load_active=first_store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(first_window)
    first_window.show()
    qtbot.waitUntil(first_window.setup.create_button.isEnabled)

    assert first_window.findChildren(QPushButton, "manualBuyButton") == []
    assert first_window.findChildren(QPushButton, "manualSellButton") == []
    assert first_window.workspace.chart.objectName() == "candlestickChart"
    assert first_window.workspace.position_basket.state_label.text() == (
        "No open Position"
    )
    assert _trade_side_effect_counts(database) == initial_side_effect_counts

    first_window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    qtbot.waitUntil(
        lambda: first_window.trade_history.basket_state.text() == "No Position history"
    )
    # No session yet: the setup form is what the workspace offers.
    assert first_window.setup.isVisible()
    first_window.workspace.show_tab(WorkspaceTab.POSITION_BASKET)

    _enter_form_values(first_window, case)
    click(first_window.setup.create_button)
    qtbot.waitUntil(lambda: create_calls == 1)

    durable_session = SQLiteActivePaperSessions(
        SQLiteDatabase(database_path)
    ).get_active()
    assert durable_session is not None
    assert create_calls == 1
    assert durable_session.config.session_id == expected_session_id
    assert durable_session.created_at_utc == expected_created_at
    _assert_durable_session_matches_case(
        durable_session,
        case,
        expected_session_id=expected_session_id,
        expected_created_at=expected_created_at,
    )
    assert first_window.setup.timeframe.currentData() == case.timeframe
    assert _active_session_count(database) == 1
    assert _trade_side_effect_counts(database) == initial_side_effect_counts

    first_window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    assert (
        first_window.workspace.tab_pages.currentWidget()
        is first_window.workspace.trade_history
    )
    first_window.workspace.show_tab(WorkspaceTab.POSITION_BASKET)

    second_create_use_case = CreatePaperSession(
        create_active=SQLiteActivePaperSessions(SQLiteDatabase(database_path)).create,
        session_ids=lambda: UUID("00000000-0000-0000-0000-000000000116"),
        clock=lambda: expected_created_at,
    )

    second_outcome = second_create_use_case.execute(_setup_values(case))

    assert second_outcome.created is False
    assert second_outcome.session == durable_session
    _assert_durable_session_matches_case(
        second_outcome.session,
        case,
        expected_session_id=expected_session_id,
        expected_created_at=expected_created_at,
    )
    assert _active_session_count(database) == 1
    assert _trade_side_effect_counts(database) == initial_side_effect_counts

    first_window.close()
    qtbot.waitUntil(first_window.isHidden)

    restarted_store = SQLiteActivePaperSessions(SQLiteDatabase(database_path))
    restarted_session = restarted_store.get_active()
    assert restarted_session is not None
    _assert_durable_session_matches_case(
        restarted_session,
        case,
        expected_session_id=expected_session_id,
        expected_created_at=expected_created_at,
    )

    def create_must_not_run(
        values: PaperSessionSetupValues,
    ) -> PaperSessionCreateOutcome:
        pytest.fail("restart must restore the active Paper Session without create")

    restarted_window = MainWindow(
        create_session=create_must_not_run,
        load_active=restarted_store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(restarted_window)
    restarted_window.show()
    qtbot.waitUntil(
        lambda: restarted_window.setup.timeframe.currentData() == case.timeframe
    )

    assert restarted_window.setup.timeframe.currentData() == case.timeframe
    # The Cooldown is pinned for the Session, so the restarted form has to
    # show the one it is running rather than the one it opens on.
    assert (
        restarted_window.setup.cooldown_mode.currentData() == case.cooldown.mode.value
    )
    assert restarted_window.setup.cooldown_days_row.isHidden() is (
        case.cooldown.days is None
    )
    if case.cooldown.days is not None:
        assert restarted_window.setup.cooldown_days.value() == case.cooldown.days
    assert _active_session_count(database) == 1
    assert _trade_side_effect_counts(database) == initial_side_effect_counts

    restored_history_side_effect_counts = _trade_side_effect_counts(database)
    restarted_window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    restarted_history = restarted_window.trade_history
    qtbot.waitUntil(
        lambda: restarted_history.basket_state.text() == "No Position history"
    )
    assert restarted_window.overview.isVisible() is False
    assert _trade_side_effect_counts(database) == restored_history_side_effect_counts


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _SessionCase("spot", "15m", 12, None, CooldownPolicy.until_cancelled()),
            id="spot",
        ),
        pytest.param(
            _SessionCase("futures", "1h", 14, 4, CooldownPolicy.until_cancelled()),
            id="futures",
        ),
    ],
)
def test_desktop_fake_lifecycle_has_no_trade_storage_side_effects(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    case: _SessionCase,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    durable = (
        CreatePaperSession(create_active=store.create)
        .execute(_setup_values(case))
        .session
    )
    initial_counts = _trade_side_effect_counts(database)
    start_entered = Event()
    release_start = Event()
    stop_entered = Event()
    release_stop = Event()

    def fake_start(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        start_entered.set()
        assert release_start.wait(timeout=1)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def fake_stop(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        stop_entered.set()
        assert release_stop.wait(timeout=1)
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.STOPPED,
            )
        )

    window = MainWindow(
        create_session=lambda values: pytest.fail("create must not run"),
        load_active=lambda: durable,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=fake_start, stop=fake_stop),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: window.workspace.bot_control_widget.start_button.isEnabled()
    )

    click(window.workspace.bot_control_widget.start_button)
    qtbot.waitUntil(start_entered.is_set)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.STARTING)
    release_start.set()
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)

    assert window.workspace.bot_control_widget.stop_button.text() == "Stop Bot"
    assert window.findChildren(QPushButton, "manualBuyButton") == []
    assert window.findChildren(QPushButton, "manualSellButton") == []
    assert _trade_side_effect_counts(database) == initial_counts

    click(window.workspace.bot_control_widget.stop_button)
    confirmation = window.findChild(QMessageBox)
    assert confirmation is not None
    assert confirmation.defaultButton().text() == "Cancel"
    confirm_button = next(
        button for button in confirmation.buttons() if button.text() == "Stop Bot"
    )
    click(confirm_button)
    qtbot.waitUntil(stop_entered.is_set)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.STOPPING)
    release_stop.set()
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.STOPPED)

    assert _trade_side_effect_counts(database) == initial_counts


def test_desktop_session_storage_unavailable_fails_closed(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    unavailable_path = tmp_path / "unavailable-session-storage"
    unavailable_path.mkdir()
    unavailable_store = SQLiteActivePaperSessions(SQLiteDatabase(unavailable_path))
    create_calls = 0

    def create_must_not_run(
        values: PaperSessionSetupValues,
    ) -> PaperSessionCreateOutcome:
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("create must not run while session storage is unavailable")

    window = MainWindow(
        create_session=create_must_not_run,
        load_active=unavailable_store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.unavailable_panel.isVisible)

    assert create_calls == 0
    assert window.unavailable_message.text() == "Session storage is unavailable"
    assert str(unavailable_path) not in window.unavailable_message.text()
    assert "sqlite" not in window.unavailable_message.text().casefold()
    assert not window.setup.isVisible()
    assert not window.overview.isVisible()

    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )
    assert window.workspace.tab_pages.currentWidget() is window.workspace.trade_history
    assert window.unavailable_panel.isVisible()


def test_desktop_session_sqlite_write_failure_after_setup_fails_closed(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _block_network(monkeypatch)
    database_path = tmp_path / "tiewtrade.sqlite3"
    durable_database = SQLiteDatabase(database_path)
    durable_database.migrate()
    failed_store = SQLiteActivePaperSessions(_CommitFailingDatabase(database_path))
    create_use_case = CreatePaperSession(create_active=failed_store.create)
    window = MainWindow(
        create_session=create_use_case.execute,
        load_active=failed_store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    _enter_form_values(
        window,
        _SessionCase(
            market_type="spot",
            timeframe="15m",
            max_entries=12,
            futures_leverage=None,
            cooldown=CooldownPolicy.until_cancelled(),
        ),
    )
    click(window.setup.create_button)
    qtbot.waitUntil(window.unavailable_panel.isVisible)

    assert window.unavailable_message.text() == "Session storage is unavailable"
    assert window.setup.capital_per_entry.text() == "200000"
    assert window.setup.timeframe.currentData() == "15m"
    assert window.setup.max_entries.value() == 12
    assert not window.overview.isVisible()
    assert SQLiteActivePaperSessions(durable_database).get_active() is None


def test_desktop_session_validation_failure_after_setup_preserves_input(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    create_use_case = CreatePaperSession(create_active=store.create)
    window = MainWindow(
        create_session=create_use_case.execute,
        load_active=store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    _enter_form_values(
        window,
        _SessionCase(
            market_type="spot",
            timeframe="15m",
            max_entries=12,
            futures_leverage=None,
            cooldown=CooldownPolicy.until_cancelled(),
        ),
    )
    window.setup.capital_per_entry.setText("0")
    click(window.setup.create_button)
    qtbot.waitUntil(
        lambda: window.setup.capital_per_entry_error.text() == "Amount must be positive"
    )

    assert window.setup.isVisible()
    assert window.setup.capital_per_entry.text() == "0"
    assert window.setup.timeframe.currentData() == "15m"
    assert window.setup.max_entries.value() == 12
    assert not window.overview.isVisible()
    assert store.get_active() is None


def test_desktop_session_setup_sources_exclude_runtime_and_sensitive_imports() -> None:
    ui_source_paths = tuple(sorted(Path("src/tiewtrade/ui").rglob("*.py")))
    session_setup_source_paths = (
        Path("src/tiewtrade/application/paper_session_setup.py"),
    )
    desktop_composition_paths = (
        Path("src/tiewtrade/desktop_main.py"),
        Path("src/tiewtrade/desktop_paper_runtime.py"),
    )
    ui_forbidden_prefixes = (
        "aiohttp",
        "keyring",
        "tiewtrade.application.paper_futures_session",
        "tiewtrade.application.paper_spot_market_data",
        "tiewtrade.application.paper_spot_session",
        "tiewtrade.application.public_market_data_runtime",
        "tiewtrade.execution",
        "tiewtrade.integrations.binance",
        "tiewtrade.integrations.credentials",
        "tiewtrade.integrations.keyring",
        "tiewtrade.integrations.private_api",
        "tiewtrade.strategies",
        "tiewtrade.trading.entry_pair",
    )
    composition_forbidden_prefixes = (
        "aiohttp",
        "keyring",
        "tiewtrade.application.paper_futures_session",
        "tiewtrade.application.paper_spot_market_data",
        "tiewtrade.application.paper_spot_session",
        "tiewtrade.application.public_market_data_runtime",
        "tiewtrade.integrations.binance",
        "tiewtrade.integrations.credentials",
        "tiewtrade.integrations.keyring",
        "tiewtrade.integrations.private_api",
        "tiewtrade.execution",
        "tiewtrade.market_data.candle_pipeline",
        "tiewtrade.market_data.candle_source",
        "tiewtrade.market_data.completed_candle_stream",
        "tiewtrade.market_data.runtime",
        "tiewtrade.strategies.rsi_step_grid.strategy",
        "tiewtrade.trading.entry_pair",
    )
    desktop_forbidden_prefixes = (
        "aiohttp",
        "keyring",
        "tiewtrade.application.live",
        "tiewtrade.execution.live",
        "tiewtrade.integrations.credentials",
        "tiewtrade.integrations.keyring",
        "tiewtrade.integrations.private_api",
        "tiewtrade.live",
    )

    assert ui_source_paths
    assert not _forbidden_imports(
        ui_source_paths,
        forbidden_prefixes=ui_forbidden_prefixes,
        restrict_market_data_to_config=True,
    )
    assert not _forbidden_imports(
        session_setup_source_paths,
        forbidden_prefixes=composition_forbidden_prefixes,
        restrict_market_data_to_config=False,
    )
    for composition_path in desktop_composition_paths:
        desktop_imports = _imported_modules(composition_path)
        assert not {
            module
            for module in desktop_imports
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in desktop_forbidden_prefixes
            )
        }
    assert not _sensitive_terms(
        ui_source_paths + session_setup_source_paths + desktop_composition_paths
    )


def test_line_notification_composition_is_the_only_place_a_token_is_reached() -> None:
    """The credential seam is one file, and it faces away from the screen.

    `desktop_main` composes everything and is forbidden a keyring for exactly
    that reason: a composition root that can read a token is a composition
    root that can hand one anywhere. This module is where the keyring is
    reached instead, and it may not import `ui` — nothing on screen gets to
    ask it for the value it holds.
    """
    composition_path = Path("src/tiewtrade/line_notification_composition.py")
    imports = _imported_modules(composition_path)

    assert "tiewtrade.integrations.keyring.line_notifications" in imports
    assert not {module for module in imports if module.startswith("tiewtrade.ui")}
    assert "keyring" not in _imported_modules(Path("src/tiewtrade/desktop_main.py"))


def test_desktop_session_setup_smoke_composes_without_network(
    qapp: QApplication,
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    create_use_case = CreatePaperSession(create_active=store.create)
    window = MainWindow(
        create_session=create_use_case.execute,
        load_active=store.get_active,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
    )
    qtbot.addWidget(window)

    window.show()
    qapp.processEvents()
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())

    assert not window.isVisible()


def _fake_running_workspace(
    session: ConfiguredPaperSession,
    *,
    observed_at: datetime,
) -> TradingWorkspaceSnapshot:
    configured = configured_workspace_snapshot(session, observed_at_utc=observed_at)
    with_facts = replace(
        configured,
        position_basket=ready_position_basket_tab(
            BasketSnapshot(
                symbol="BTCUSDT",
                market_type=session.config.market_type.value,
                position_side="long",
                entry_count=1,
                entries=basket_entries(
                    "66000.1250", quantity="0.00150000", current_price="66321.1200"
                ),
                total_quantity=Decimal("0.00150000"),
                average_entry_price=Decimal("66000.1250"),
                current_price=Decimal("66321.1200"),
                take_profit_price=Decimal("67000.0000"),
                targets=basket_targets(
                    basket_entries(
                        "66000.1250", quantity="0.00150000", current_price="66321.1200"
                    )
                ),
                unrealized_pnl=Decimal("1.92600000"),
                entry_gate=BasketEntryGate.OPEN,
                updated_at_utc=observed_at,
            ),
            observed_at_utc=observed_at,
        ),
    )
    return workspace_with_runtime_state(
        with_facts,
        BotRuntimeState.RUNNING,
        data_freshness=DataFreshness.FRESH,
    )


def _manual_order_buttons(window: MainWindow) -> tuple[QPushButton, ...]:
    object_names = {"manualBuyButton", "manualSellButton"}
    manual_texts = {"buy", "sell"}
    return tuple(
        button
        for button in window.findChildren(QPushButton)
        if button.objectName() in object_names
        or button.text().strip().casefold() in manual_texts
    )


def _enter_form_values(window: MainWindow, case: _SessionCase) -> None:
    setup = window.setup
    setup.market_type.setCurrentIndex(setup.market_type.findData(case.market_type))
    setup.timeframe.setCurrentIndex(setup.timeframe.findData(case.timeframe))
    setup.capital_per_entry.setText("200000")
    setup.max_entries.setValue(case.max_entries)
    setup.cooldown_mode.setCurrentIndex(
        setup.cooldown_mode.findData(case.cooldown.mode.value)
    )
    if case.cooldown.days is not None:
        setup.cooldown_days.setValue(case.cooldown.days)
    if case.market_type == "spot":
        return
    assert case.futures_leverage is not None
    setup.leverage.setValue(case.futures_leverage)


def _setup_values(case: _SessionCase) -> PaperSessionSetupValues:
    return PaperSessionSetupValues(
        market_type=case.market_type,
        symbol="BTCUSDT",
        timeframe=case.timeframe,
        capital_per_entry="200000",
        max_entries=str(case.max_entries),
        futures_leverage=(
            None if case.futures_leverage is None else str(case.futures_leverage)
        ),
    )


def _paper_runtime_candles(
    config: MarketDataConfig,
) -> tuple[tuple[Candle, ...], tuple[Candle, ...]]:
    assert config.timeframe == "5m"
    # A longer warm-up starts earlier rather than running later: it still ends
    # on the candle before the live ones, closing at 86, so the prices and
    # times under test do not move with the length the Preset asks for.
    live_from = 15
    warm_up_count = RsiStepGridPreset.v1().minimum_warm_up_candles
    warm_up: list[Candle] = []
    for index in range(live_from - warm_up_count, live_from):
        close = Decimal(86 + live_from - 1 - index)
        warm_up.append(_paper_runtime_candle(config, index=index, close=close))

    close = Decimal("86")
    live: list[Candle] = []
    for index in range(live_from, live_from + 11):
        close += Decimal("1")
        live.append(_paper_runtime_candle(config, index=index, close=close))
    return tuple(warm_up), tuple(live)


def _paper_runtime_candle(
    config: MarketDataConfig,
    *,
    index: int,
    close: Decimal,
) -> Candle:
    opened = close - Decimal("0.5")
    return Candle(
        symbol=config.symbol,
        timeframe=config.timeframe,
        open_time=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(minutes=index * 5),
        open=opened,
        high=close + Decimal("1"),
        low=opened - Decimal("1"),
        close=close,
    )


def _acceptance_symbol_rules(symbol: str) -> SymbolRules:
    return SymbolRules(
        symbol=symbol,
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


def _paper_runtime_result_for_ui(
    current: BotControlSnapshot,
    result: BotLifecycleResult,
) -> BotLifecycleResult:
    header = result.workspace.header
    assert header is not None
    return BotLifecycleResult(
        workspace=workspace_with_runtime_state(
            current.workspace,
            header.runtime_state,
            data_freshness=(
                None
                if header.runtime_state is BotRuntimeState.BLOCKED
                else header.data_freshness
            ),
        ),
        blocked_reason=result.blocked_reason,
    )


def _assert_durable_session_matches_case(
    session: ConfiguredPaperSession,
    case: _SessionCase,
    *,
    expected_session_id: UUID,
    expected_created_at: datetime,
) -> None:
    config = session.config

    assert config.session_id == expected_session_id
    assert config.trade_mode is TradeMode.PAPER
    assert config.market_type is MarketType(case.market_type)
    assert config.preset_version == "rsi-step-grid-v1"
    assert config.capital_per_entry == Decimal("200000")
    assert config.entry_policy.max_entries == case.max_entries
    assert config.entry_policy.cooldown == case.cooldown
    assert session.market_data.symbol == "BTCUSDT"
    assert session.market_data.timeframe == case.timeframe
    assert session.created_at_utc == expected_created_at
    assert session.ended_at_utc is None

    if case.market_type == "spot":
        # Spot carries no policy of its own: all of its capital is traded.
        assert config.futures_policy is None
        return

    assert case.futures_leverage is not None
    assert config.futures_policy is not None
    assert config.futures_policy.version == "paper-futures-v1"
    assert config.futures_policy.leverage == case.futures_leverage
    assert config.futures_policy.position_mode is PositionMode.ONE_WAY
    assert config.futures_policy.margin_mode is MarginMode.CROSS
    assert config.futures_policy.trading_capital_ratio == Decimal("0.5")
    assert config.futures_policy.collateral_buffer_ratio == Decimal("0.5")


def _assert_overview_matches_case(
    window: MainWindow,
    case: _SessionCase,
    *,
    expected_session_id: UUID,
    expected_created_at: datetime,
) -> None:
    assert _runtime_state(window) is BotRuntimeState.CONFIGURED
    assert window.overview.isVisible()
    assert window.workspace.bot_control_widget.start_button.isVisible()
    assert not window.workspace.bot_control_widget.start_button.isEnabled()
    assert window.overview.market_value.text() == case.market_type.title()
    assert window.overview.symbol_value.text() == "BTCUSDT"
    assert window.overview.timeframe_value.text() == case.timeframe
    assert window.overview.preset_value.text() == "RSI Step Grid"
    assert window.overview.capital_per_entry_value.text() == "200000 USDT"
    assert window.overview.max_entries_value.text() == str(case.max_entries)
    assert window.overview.created_at_value.text() == display_timestamp(
        expected_created_at
    )

    if case.market_type == "spot":
        # Spot has no policy rows at all, rather than rows reading blank.
        assert "Leverage" not in window.overview.visible_labels
        assert "Margin Mode" not in window.overview.visible_labels
        return

    assert case.futures_leverage is not None
    assert window.overview.margin_mode_value.text() == "Cross Margin"
    assert window.overview.position_mode_value.text() == "One-way Mode"


def _active_session_count(database: SQLiteDatabase) -> int:
    with database.connect() as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM bot_sessions WHERE ended_at_utc IS NULL"
            ).fetchone()[0]
        )


def _trade_side_effect_counts(database: SQLiteDatabase) -> tuple[int, int]:
    with database.connect() as connection:
        basket_count = connection.execute(
            "SELECT COUNT(*) FROM basket_results"
        ).fetchone()[0]
        fill_count = connection.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[
            0
        ]
    return int(basket_count), int(fill_count)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class _CommitFailingConnection(sqlite3.Connection):
    def commit(self) -> None:
        raise sqlite3.OperationalError("injected commit failure")


class _CommitFailingDatabase(SQLiteDatabase):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, factory=_CommitFailingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _imported_modules(source_path: Path) -> set[str]:
    source = ast.parse(source_path.read_text(encoding="utf-8"))
    return {
        module for node in ast.walk(source) for module in _modules_from_import(node)
    }


def _forbidden_imports(
    source_paths: tuple[Path, ...],
    *,
    forbidden_prefixes: tuple[str, ...],
    restrict_market_data_to_config: bool,
) -> set[str]:
    imported_modules = {
        module
        for source_path in source_paths
        for module in _imported_modules(source_path)
    }
    forbidden = {
        module
        for module in imported_modules
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in forbidden_prefixes
        )
    }
    forbidden.update(
        module
        for module in imported_modules
        if any(
            part == "runtime" or part.endswith("_runtime") for part in module.split(".")
        )
    )
    if restrict_market_data_to_config:
        forbidden.update(
            module
            for module in imported_modules
            if module.startswith("tiewtrade.market_data")
            and module != "tiewtrade.market_data.config"
        )
    return forbidden


def _modules_from_import(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return (node.module,)
    return ()


def _sensitive_terms(source_paths: tuple[Path, ...]) -> set[str]:
    forbidden_fragments = (
        "api_key",
        "apikey",
        "api_secret",
        "apisecret",
        "credential",
        "keyring",
        "private_api",
        "privateapi",
        "secret",
    )
    terms: set[str] = set()
    for source_path in source_paths:
        source = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(source):
            for term in _ast_terms(node):
                normalized = term.casefold()
                if any(fragment in normalized for fragment in forbidden_fragments):
                    terms.add(term)
    return terms


def _ast_terms(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (node.attr,)
    if isinstance(node, ast.arg):
        return (node.arg,)
    if isinstance(node, ast.alias):
        return (node.asname or node.name,)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    return ()


def _block_network(monkeypatch: MonkeyPatch) -> None:
    def fail_network_attempt(*args: object, **kwargs: object) -> None:
        raise AssertionError("Desktop Session Setup must not attempt network access")

    monkeypatch.setattr(socket, "create_connection", fail_network_attempt)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network_attempt)
    monkeypatch.setattr(socket.socket, "connect", fail_network_attempt)


@pytest.mark.parametrize("market_type", ["spot", "futures"])
def test_the_whole_form_fits_the_smallest_window(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    market_type: str,
) -> None:
    """Every setting is on the form at once, so all of it has to fit.

    There is about 90px of room left over. Nothing folds away any more, so
    a field added later spends that room for good — which is why this is
    pinned rather than left to be noticed.
    """
    _block_network(monkeypatch)
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    history = SQLiteTradeHistory(database)
    window = MainWindow(
        create_session=CreatePaperSession(
            create_active=SQLiteActivePaperSessions(database).create
        ).execute,
        load_active=SQLiteActivePaperSessions(database).get_active,
        list_basket_history=history.list_basket_history,
        list_trade_fills=history.list_trade_fills,
    )
    qtbot.addWidget(window)
    window.setFixedSize(1024, 700)
    window.show()
    qtbot.waitUntil(window.setup.isVisible)
    # The form is held while the active Session is looked for.
    qtbot.waitUntil(window.setup.create_button.isEnabled)

    window.setup.market_type.setCurrentIndex(
        window.setup.market_type.findData(market_type)
    )

    scroll = window.workspace.bot_control_scroll
    assert scroll.verticalScrollBar().maximum() == 0
    # The button the form exists to reach is on screen, not below the fold.
    start = window.setup.create_button
    assert (
        scroll.viewport()
        .rect()
        .contains(QRect(start.mapTo(scroll.viewport(), QPoint(0, 0)), start.size()))
    )
