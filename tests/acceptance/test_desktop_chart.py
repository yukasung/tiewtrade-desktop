import asyncio
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import TypeVar

from PySide6.QtWidgets import QPushButton
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

from tests.support.paper_session_setup import configured_spot_session
from tests.support.qt_interactions import click
from tests.support.trade_history_records import trade_fill
from tests.support.trade_history_ui import (
    empty_basket_history,
    empty_trade_fills,
)
from tiewtrade.application.bot_control import (
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.application.paper_runtime import PaperRuntimeController
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
)
from tiewtrade.application.paper_spot_session import (
    PaperSpotSession,
    PaperSpotSessionSnapshot,
)
from tiewtrade.application.trading_workspace import BotRuntimeState, DataFreshness
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataKlineUpdate
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import FillSide
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.main_window import BotActions, ChartSources, MainWindow
from tiewtrade.ui.trading_workspace import WorkspaceTab

_T = TypeVar("_T")
_FORMING_OBSERVED_AT = datetime(2026, 8, 2, 10, 3, tzinfo=UTC)
_CLOSED_RECEIVED_AT = datetime(2026, 8, 2, 10, 5, tzinfo=UTC)


class _ImmediateScheduler:
    def now(self) -> datetime:
        return _CLOSED_RECEIVED_AT

    async def sleep(self, seconds: float) -> None:
        del seconds
        await asyncio.sleep(0)

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T:
        del timeout
        return await awaitable


class _QueuedPaperCandleSource:
    def __init__(
        self,
        *,
        config: MarketDataConfig,
        warm_up: tuple[Candle, ...],
    ) -> None:
        self._config = config
        self._warm_up = warm_up
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[MarketDataKlineUpdate] | None = None
        self.stream_ready = Event()
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
        assert completed_before == _CLOSED_RECEIVED_AT
        return self._warm_up

    async def load_range(
        self,
        config: MarketDataConfig,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        del config, start, end
        raise AssertionError("contiguous acceptance candles must not backfill")

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        assert config == self._config
        return self._stream_klines()

    async def close(self) -> None:
        self.closed.set()

    def emit(self, candle: Candle, *, is_closed: bool) -> None:
        assert self.stream_ready.wait(1)
        assert self._loop is not None
        assert self._queue is not None
        asyncio.run_coroutine_threadsafe(
            self._queue.put(MarketDataKlineUpdate(candle=candle, is_closed=is_closed)),
            self._loop,
        ).result(timeout=1)

    async def _stream_klines(self) -> AsyncIterator[MarketDataKlineUpdate]:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self.stream_ready.set()
        while True:
            yield await self._queue.get()


def _runtime_state(window: MainWindow) -> BotRuntimeState | None:
    """The state the lifecycle reached, or None before a session exists."""
    snapshot = window._lifecycle_workflow._snapshot
    return None if snapshot is None else snapshot.state


def unused_create(
    values: PaperSessionSetupValues,
) -> PaperSessionCreateOutcome:
    del values
    raise AssertionError("create is not used")


def test_desktop_shows_configured_session_chart_without_manual_order_controls(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()

    async def load_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
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
    window.resize(1200, 700)
    window.show()

    qtbot.waitUntil(
        lambda: (
            window.workspace.chart._snapshot is not None
            and window.workspace.chart._snapshot.state is ChartReadState.EMPTY
        )
    )

    assert window.workspace.chart.isVisible()
    assert window.workspace.bot_control.isVisible()
    assert window.findChildren(QPushButton, "manualBuyButton") == []
    assert window.findChildren(QPushButton, "manualSellButton") == []


def test_chart_failure_does_not_stop_runtime_or_trade_history(qtbot: QtBot) -> None:
    session = configured_spot_session()

    async def fail_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        del configured, chart_range
        raise RuntimeError("raw public transport failure")

    def start_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    def stop_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
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
        chart=ChartSources(load=fail_chart),
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(
        lambda: (
            window.workspace.chart._snapshot is not None
            and window.workspace.chart._snapshot.state is ChartReadState.UNAVAILABLE
        )
    )
    chart_snapshot = window.workspace.chart._snapshot
    assert chart_snapshot is not None
    assert chart_snapshot.message == "Chart is unavailable"

    click(window.workspace.bot_control_widget.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    window.workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No Position history"
    )

    assert window.workspace.chart.retry_button.isVisible()
    assert window.workspace.bot_control_widget.stop_button.isEnabled()
    assert window.workspace.tab_pages.currentWidget() is window.workspace.trade_history


def test_runtime_candles_reach_chart_only_from_current_generation(
    qtbot: QtBot,
) -> None:
    session = configured_spot_session()
    relay = RuntimeSnapshotRelay()
    relayed: list[Candle] = []
    refreshed: list[Candle] = []
    relay.completed_candle_ready.connect(
        lambda candle, _generation: relayed.append(candle)
    )
    chart_snapshots: list[ChartSnapshot] = []

    def start_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        return BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                snapshot.workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
        )

    async def load_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        return ChartSnapshot(
            session=configured,
            chart_range=chart_range,
            observed_at_utc=chart_range.end,
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
        )

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        refreshed.append(completed)
        duration = current.chart_range.end - current.chart_range.start
        next_range = ChartRange(completed.close_time - duration, completed.close_time)
        fill = trade_fill(
            fill_id="runtime-buy",
            side=FillSide.BUY,
            session_id=configured.config.session_id,
            filled_at_utc=completed.open_time,
        )
        return ChartSnapshot(
            session=configured,
            chart_range=next_range,
            observed_at_utc=completed.close_time,
            candles=(completed,),
            fills=(fill,),
            state=ChartReadState.READY,
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start_bot, runtime_snapshots=relay),
        chart=ChartSources(
            load=load_chart,
            refresh=refresh_chart,
            clock=lambda: datetime(2026, 8, 2, 10, 3, tzinfo=UTC),
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.workspace.chart._snapshot is not None)
    window._chart_workflow.snapshot_changed.connect(chart_snapshots.append)
    click(window.workspace.bot_control_widget.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    stale_publisher = relay.new_generation()
    publisher = relay.new_generation()
    forming = Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("104"),
        low=Decimal("99"),
        close=Decimal("102"),
    )
    completed = Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("106"),
        low=Decimal("99"),
        close=Decimal("103"),
    )

    stale_publisher.in_progress_candle(forming)
    publisher.in_progress_candle(forming)
    qtbot.waitUntil(
        lambda: (
            window.workspace.chart._snapshot is not None
            and window.workspace.chart._snapshot.in_progress_candle == forming
        )
    )
    publisher.completed_candle(completed)

    qtbot.waitUntil(lambda: relayed == [completed])
    qtbot.waitUntil(lambda: refreshed == [completed])
    qtbot.waitUntil(lambda: window.workspace.chart.marker_labels == ("B",))
    snapshot = window.workspace.chart._snapshot
    assert snapshot is not None
    assert snapshot.candles == (completed,)
    assert snapshot.in_progress_candle is None
    assert sum(item.in_progress_candle == forming for item in chart_snapshots) == 1


def test_running_paper_session_shows_open_candle_then_commits_closed_candle(
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    assert SQLiteActivePaperSessions(database).create(session).created
    completed_before_open = _completed_chart_candles(session.market_data)
    source = _QueuedPaperCandleSource(
        config=session.market_data,
        warm_up=completed_before_open,
    )
    relay = RuntimeSnapshotRelay()
    controller: PaperRuntimeController | None = None
    processed_strategy_candles: list[Candle] = []
    original_process_completed = PaperSpotSession.process_completed_candle

    def record_strategy_candle(
        application: PaperSpotSession,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> PaperSpotSessionSnapshot:
        processed_strategy_candles.append(candle)
        return original_process_completed(
            application,
            candle,
            received_at=received_at,
        )

    monkeypatch.setattr(
        PaperSpotSession,
        "process_completed_candle",
        record_strategy_candle,
    )

    async def load_chart(
        configured: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        assert configured == session
        assert chart_range.end == datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
        return ChartSnapshot(
            session=configured,
            chart_range=chart_range,
            observed_at_utc=_FORMING_OBSERVED_AT,
            candles=completed_before_open,
            fills=(),
            state=ChartReadState.READY,
        )

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        assert configured == session
        return ChartSnapshot(
            session=configured,
            chart_range=ChartRange(
                current.chart_range.start + configured.market_data.interval,
                completed.close_time,
            ),
            observed_at_utc=_CLOSED_RECEIVED_AT,
            candles=(*current.candles, completed),
            fills=current.fills,
            state=ChartReadState.READY,
        )

    def start_bot(snapshot: BotControlSnapshot) -> BotLifecycleResult:
        nonlocal controller
        publisher = relay.new_generation()
        controller = PaperRuntimeController(
            lifecycle=SQLitePaperRuntimeLifecycle(database),
            trade_history=SQLiteTradeHistory(database),
            symbol_rules=_symbol_rules(),
            source_factory=lambda _endpoints: source,
            scheduler=_ImmediateScheduler(),
            snapshot_callback=lambda _workspace: None,
            completed_candle_callback=publisher.completed_candle,
            in_progress_candle_callback=publisher.in_progress_candle,
            clock=lambda: _CLOSED_RECEIVED_AT,
        )
        return _paper_runtime_result_for_ui(
            snapshot,
            controller.start(snapshot.session),
        )

    window = MainWindow(
        create_session=unused_create,
        load_active=lambda: session,
        list_basket_history=empty_basket_history,
        list_trade_fills=empty_trade_fills,
        bot=BotActions(start=start_bot, runtime_snapshots=relay),
        chart=ChartSources(
            load=load_chart, refresh=refresh_chart, clock=lambda: _FORMING_OBSERVED_AT
        ),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(
        lambda: (
            window.workspace.chart._snapshot is not None
            and window.workspace.chart._snapshot.state is ChartReadState.READY
        )
    )
    chart_snapshots: list[ChartSnapshot] = []
    window._chart_workflow.snapshot_changed.connect(chart_snapshots.append)
    click(window.workspace.bot_control_widget.start_button)
    qtbot.waitUntil(lambda: _runtime_state(window) is BotRuntimeState.RUNNING)
    assert source.stream_ready.wait(1)

    forming = _chart_candle(
        open=Decimal("100"), high=Decimal("104"), close=Decimal("102")
    )
    closed = _chart_candle(
        open=Decimal("100"), high=Decimal("106"), close=Decimal("103")
    )

    try:
        source.emit(forming, is_closed=False)
        qtbot.waitUntil(
            lambda: (
                window.workspace.chart._snapshot is not None
                and window.workspace.chart._snapshot.in_progress_candle == forming
            )
        )
        open_snapshot = window.workspace.chart._snapshot
        assert open_snapshot is not None
        assert open_snapshot.in_progress_candle == forming
        assert open_snapshot.candles == completed_before_open
        assert processed_strategy_candles == []

        source.emit(closed, is_closed=True)
        qtbot.waitUntil(lambda: processed_strategy_candles == [closed])
        qtbot.waitUntil(
            lambda: (
                window.workspace.chart._snapshot is not None
                and window.workspace.chart._snapshot.candles[-1:] == (closed,)
                and window.workspace.chart._snapshot.in_progress_candle is None
            )
        )

        committed_snapshot = window.workspace.chart._snapshot
        assert committed_snapshot is not None
        assert committed_snapshot.candles == (*completed_before_open, closed)
        assert committed_snapshot.in_progress_candle is None
        assert processed_strategy_candles == [closed]
        assert open_snapshot in chart_snapshots
        assert committed_snapshot in chart_snapshots
    finally:
        if controller is not None:
            controller.stop(session)

    assert source.closed.wait(1)


def _completed_chart_candles(config: MarketDataConfig) -> tuple[Candle, ...]:
    first_open = datetime(2026, 8, 2, 8, 45, tzinfo=UTC)
    candles: list[Candle] = []
    for index in range(15):
        close = Decimal("100") + Decimal(index)
        candles.append(
            Candle(
                symbol=config.symbol,
                timeframe=config.timeframe,
                open_time=first_open + config.interval * index,
                open=close - Decimal("0.5"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
            )
        )
    return tuple(candles)


def _chart_candle(*, open: Decimal, high: Decimal, close: Decimal) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        open=open,
        high=high,
        low=Decimal("99"),
        close=close,
    )


def _symbol_rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTCUSDT",
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
            data_freshness=header.data_freshness,
        ),
        blocked_reason=result.blocked_reason,
    )
