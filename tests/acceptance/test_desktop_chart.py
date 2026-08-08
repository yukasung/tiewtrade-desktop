from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from tests.support.paper_session_setup import configured_spot_session
from tests.support.qt_interactions import click
from tests.support.trade_history_records import trade_fill
from tests.support.trade_history_ui import empty_basket_page, empty_fills
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
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
)
from tiewtrade.application.trading_workspace import BotRuntimeState, DataFreshness
from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.trade_history import FillSide
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.main_window import MainWindow


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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        load_chart=load_chart,
        chart_clock=lambda: datetime(2026, 8, 2, 10, 3, tzinfo=UTC),
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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        load_chart=fail_chart,
        start_bot=start_bot,
        stop_bot=stop_bot,
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
    qtbot.waitUntil(lambda: window.workspace.header_runtime.text() == "Running")
    window.workspace.tabs.setCurrentWidget(window.trade_history)
    qtbot.waitUntil(
        lambda: window.trade_history.basket_state.text() == "No trade history"
    )

    assert window.workspace.chart.retry_button.isVisible()
    assert window.workspace.bot_control_widget.stop_button.isEnabled()
    assert window.workspace.tabs.currentWidget() is window.trade_history


def test_runtime_candles_reach_chart_only_from_current_generation(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = configured_spot_session()
    relay = RuntimeSnapshotRelay()
    relayed: list[Candle] = []
    refreshed: list[Candle] = []
    relay.completed_candle_ready.connect(
        lambda candle, _generation: relayed.append(candle)
    )
    in_progress: list[Candle] = []

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
        list_baskets=empty_basket_page,
        list_fills=empty_fills,
        load_chart=load_chart,
        refresh_chart=refresh_chart,
        runtime_snapshots=relay,
        chart_clock=lambda: datetime(2026, 8, 2, 10, 3, tzinfo=UTC),
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.workspace.chart._snapshot is not None)
    monkeypatch.setattr(
        window._chart_workflow,
        "in_progress_candle",
        in_progress.append,
        raising=False,
    )
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
    publisher.completed_candle(completed)

    qtbot.waitUntil(lambda: in_progress == [forming])
    qtbot.waitUntil(lambda: relayed == [completed])
    qtbot.waitUntil(lambda: refreshed == [completed])
    qtbot.waitUntil(lambda: window.workspace.chart.marker_labels == ("Buy",))
    snapshot = window.workspace.chart._snapshot
    assert snapshot is not None
    assert snapshot.candles == (completed,)
