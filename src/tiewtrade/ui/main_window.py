from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox

from tiewtrade.application.bot_control import BotControlAction, BotControlSnapshot
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
    PaperSessionValidationError,
    validate_paper_session,
)
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.bot_lifecycle_workflow import (
    BotLifecycleWorkflow,
    LifecycleAction,
    RuntimeSnapshotRelay,
)
from tiewtrade.ui.chart_workflow import (
    ChartFactRelay,
    ChartWorkflow,
    LoadChart,
    LoadChartFills,
    RefreshChart,
)
from tiewtrade.ui.session_workflow import (
    CreateSession,
    LoadActiveSession,
    SessionWorkflow,
)
from tiewtrade.ui.trade_history_workflow import (
    ListBasketHistory,
    ListTradeFills,
    TradeHistoryWorkflow,
)
from tiewtrade.ui.trading_workspace import TradingWorkspace

WORKER_SHUTDOWN_TIMEOUT_MS = 5_000


@dataclass(frozen=True, slots=True)
class BotActions:
    """What the window may do to the Bot and to the Session running it.

    Every field is optional, because a window opened on the setup form or on
    durable history needs none of them, and a test that exercises one flow
    should not have to name the rest. Grouping them says which of the
    window's questions they answer, which a flat list of optional callables
    could not.
    """

    start: LifecycleAction | None = None
    stop: LifecycleAction | None = None
    initialize: LifecycleAction | None = None
    runtime_snapshots: RuntimeSnapshotRelay | None = None
    shutdown_runtime: Callable[[], None] | None = None
    stop_for_replacement: Callable[[ConfiguredPaperSession], None] | None = None
    close_position: Callable[[ConfiguredPaperSession], None] | None = None
    has_open_position: Callable[[ConfiguredPaperSession], bool] | None = None
    retire_session: Callable[[ConfiguredPaperSession], None] | None = None


@dataclass(frozen=True, slots=True)
class ChartSources:
    """Where the chart's candles and fills come from, and what keeps them live."""

    load: LoadChart | None = None
    refresh: RefreshChart | None = None
    load_fills: LoadChartFills | None = None
    facts: ChartFactRelay | None = None
    start_feed: Callable[[ConfiguredPaperSession], None] | None = None
    stop_feed: Callable[[], None] | None = None
    select_view: (
        Callable[[ConfiguredPaperSession, ChartViewSelection], None] | None
    ) = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


# Frozen and empty, so one instance can stand for "none of this is wired".
NO_BOT = BotActions()
NO_CHART = ChartSources()


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        create_session: CreateSession,
        load_active: LoadActiveSession,
        list_basket_history: ListBasketHistory | None = None,
        list_trade_fills: ListTradeFills | None = None,
        validate_session: Callable[[PaperSessionSetupValues], None] | None = None,
        bot: BotActions = NO_BOT,
        chart: ChartSources = NO_CHART,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._load_chart = chart.load
        self._close_position = bot.close_position
        self._has_open_position = bot.has_open_position
        self._retire_session = bot.retire_session
        self._stop_for_replacement = bot.stop_for_replacement
        self._validate_session = validate_session or validate_paper_session
        self._shutdown_runtime = bot.shutdown_runtime
        self._chart_fact_relay = chart.facts
        self._start_chart_feed = chart.start_feed
        self._select_chart_view = chart.select_view
        self._stop_chart_feed = chart.stop_feed
        self._close_sequence_started = False
        self._close_sequence_completed = False
        self._shutdown_task: BackgroundTask | None = None
        self._replacement_task: BackgroundTask | None = None
        self._runtime_shutdown_pool = QThreadPool(self)
        self._runtime_shutdown_pool.setMaxThreadCount(1)
        self.workspace = TradingWorkspace()
        self.setup = self.workspace.setup
        self.overview = self.workspace.overview
        self.trade_history = self.workspace.trade_history
        self.unavailable_panel = self.workspace.unavailable_panel
        self.unavailable_message = self.workspace.unavailable_message
        self.unavailable_retry_button = self.workspace.unavailable_retry_button
        self.setCentralWidget(self.workspace)
        self.setWindowTitle("TiewTrade")
        self.setMinimumSize(1024, 700)
        self.resize(1440, 900)
        self._pending_validation_field: str | None = None
        self._start_after_create = False
        self._pending_start_session: ConfiguredPaperSession | None = None
        self._return_to_setup_after_stop = False
        self._replacement_session: ConfiguredPaperSession | None = None
        self._replacement_values: PaperSessionSetupValues | None = None
        self._replacement_succeeded = False

        self._workflow = SessionWorkflow(
            create_session=create_session,
            load_active=load_active,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._workflow.busy_changed.connect(self._set_busy)
        self._workflow.workspace_changed.connect(self.workspace.show_workspace_snapshot)
        self._workflow.setup_required.connect(self._show_setup)
        self._workflow.session_ready.connect(self._show_session)
        self._workflow.validation_failed.connect(self._show_validation_error)
        self._workflow.unavailable.connect(self._show_unavailable)
        self.setup.create_requested.connect(self._create_requested)
        self.unavailable_retry_button.clicked.connect(self._retry_unavailable)

        self._lifecycle_workflow = BotLifecycleWorkflow(
            start_bot=bot.start,
            stop_bot=bot.stop,
            initialize_bot=bot.initialize,
            runtime_snapshots=bot.runtime_snapshots,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._lifecycle_workflow.snapshot_changed.connect(self._bot_control_changed)
        self.workspace.start_bot_requested.connect(self._lifecycle_workflow.start_bot)
        self.workspace.stop_bot_requested.connect(self._stop_requested)

        self._chart_workflow = ChartWorkflow(
            load_chart=chart.load or _unused_chart_loader,
            refresh_chart=chart.refresh,
            load_fills=chart.load_fills,
            thread_pool=self._thread_pool,
            clock=chart.clock,
            parent=self,
        )
        self._chart_workflow.snapshot_changed.connect(
            self.workspace.chart.show_snapshot
        )
        self._chart_workflow.loading_changed.connect(
            self.workspace.chart.set_view_loading
        )
        self.workspace.chart.range_requested.connect(self._chart_workflow.load_range)
        self.workspace.chart.retry_requested.connect(self._chart_workflow.retry)
        self.workspace.chart.view_requested.connect(self._chart_view_requested)
        if chart.facts is not None:
            chart.facts.completed_candle_ready.connect(
                self._chart_completed_candle,
                Qt.ConnectionType.QueuedConnection,
            )
            chart.facts.in_progress_candle_ready.connect(
                self._chart_in_progress_candle,
                Qt.ConnectionType.QueuedConnection,
            )
        self._runtime_snapshots = bot.runtime_snapshots
        if chart.facts is None and bot.runtime_snapshots is not None:
            bot.runtime_snapshots.completed_candle_ready.connect(
                self._runtime_completed_candle,
                Qt.ConnectionType.QueuedConnection,
            )
            bot.runtime_snapshots.in_progress_candle_ready.connect(
                self._runtime_in_progress_candle,
                Qt.ConnectionType.QueuedConnection,
            )

        self._history_started = False
        self._history_workflow = TradeHistoryWorkflow(
            list_basket_history=list_basket_history,
            list_trade_fills=list_trade_fills,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._wire_trade_history()
        self.workspace.trade_history_activated.connect(self._start_trade_history_once)
        self._workflow.start()

    @Slot(object)
    def _create_requested(self, raw_values: object) -> None:
        if not isinstance(raw_values, PaperSessionSetupValues):
            return

        self._pending_validation_field = None
        self.setup.clear_errors()
        if self._replacement_session is None:
            self._start_after_create = True
            self._workflow.create(raw_values)
            return
        try:
            self._validate_session(raw_values)
        except PaperSessionValidationError as error:
            self._show_validation_error(error.field, str(error))
            return
        self._replacement_values = raw_values
        persisted_position = (
            self._has_open_position(self._replacement_session)
            if self._has_open_position is not None
            else False
        )
        visible_position = (
            self._lifecycle_workflow.snapshot.workspace.basket is not None
        )
        if persisted_position or visible_position:
            answer = QMessageBox.question(
                self,
                "Close Paper Position?",
                "Close all Paper positions at the latest fresh market price "
                "before starting the new bot?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._replace_session()

    def _replace_session(self) -> None:
        session = self._replacement_session
        values = self._replacement_values
        retire_session = self._retire_session
        if session is None or values is None:
            return
        if retire_session is None:
            self.setup.show_field_error(
                "available_capital",
                "Paper Session replacement is unavailable. Restart the application.",
            )
            return
        if self._replacement_task is not None:
            return

        def stop_close_and_retire() -> None:
            if self._stop_for_replacement is not None:
                self._stop_for_replacement(session)
            if self._has_open_position is not None and self._has_open_position(session):
                if self._close_position is None:
                    raise RuntimeError("Paper position close is unavailable")
                self._close_position(session)
            retire_session(session)

        self.workspace.set_bot_busy(True)
        self._replacement_succeeded = False
        task = BackgroundTask(stop_close_and_retire)
        task.signals.succeeded.connect(self._replacement_completed)
        task.signals.failed.connect(self._replacement_failed)
        task.signals.finished.connect(self._replacement_finished)
        self._replacement_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _replacement_completed(self, _result: object) -> None:
        self._replacement_session = None
        self._replacement_succeeded = True
        self._start_after_create = True

    @Slot(object)
    def _replacement_failed(self, _error: object) -> None:
        message = "Could not stop, close, or retire the existing Paper Session. "
        if isinstance(_error, RuntimeError) and str(_error) == (
            "A fresh market price is unavailable"
        ):
            message = (
                "Current market price is not fresh. Wait for live price data, "
                "then try again."
            )
        elif isinstance(_error, RuntimeError) and str(_error) == (
            "Paper Bot must stop before it can be replaced"
        ):
            message = (
                "Paper Bot is still stopping. Wait for it to stop, then try again."
            )
        self.setup.show_field_error(
            "available_capital",
            f"{message} Try Start Paper Bot again.",
        )

    @Slot()
    def _replacement_finished(self) -> None:
        self._replacement_task = None
        if not self._replacement_succeeded:
            self.workspace.set_bot_busy(False)
            return
        self._replacement_succeeded = False
        values = self._replacement_values
        self._replacement_values = None
        if values is not None:
            self._workflow.create(values)

    @Slot()
    def _retry_unavailable(self) -> None:
        self._workflow.start()

    @Slot(bool)
    def _set_busy(self, busy: bool) -> None:
        self.workspace.set_bot_busy(busy)
        if busy or self._pending_validation_field is None:
            return
        validation_field = self._pending_validation_field
        self._pending_validation_field = None
        self.workspace.show_setup_for_validation(validation_field)

    @Slot(object)
    def _show_session(self, session: ConfiguredPaperSession) -> None:
        self._pending_validation_field = None
        start_after_create = self._start_after_create
        if start_after_create:
            self._pending_start_session = session
        self._start_after_create = False
        self._lifecycle_workflow.configure(
            session,
            inspect_startup=not start_after_create,
        )
        if self._load_chart is not None:
            self._chart_workflow.start(session)
        if self._start_chart_feed is not None:
            self._start_chart_feed(session)

    @Slot(object)
    def _bot_control_changed(self, value: object) -> None:
        if not isinstance(value, BotControlSnapshot):
            return
        self.workspace.show_bot_control_snapshot(value)
        self._chart_workflow.runtime_snapshot(value)
        if (
            self._pending_start_session == value.session
            and value.state is BotRuntimeState.CONFIGURED
            and BotControlAction.START in value.available_actions
        ):
            self._pending_start_session = None
            self._lifecycle_workflow.start_bot()
            return
        if value.state is BotRuntimeState.CONFIGURED:
            self._show_editable_setup(value)
            return
        if self._return_to_setup_after_stop and value.state is BotRuntimeState.STOPPED:
            self._return_to_setup_after_stop = False
            self._show_editable_setup(value)
            return
        if value.state is BotRuntimeState.BLOCKED:
            self._show_editable_setup(value)

    @Slot()
    def _stop_requested(self) -> None:
        self._return_to_setup_after_stop = True
        self._lifecycle_workflow.stop_bot()

    def _show_editable_setup(self, snapshot: BotControlSnapshot) -> None:
        self._replacement_session = snapshot.session
        self.setup.set_values_from_session(snapshot.session)
        self.workspace.show_setup()

    @Slot(object, int)
    def _chart_completed_candle(self, candle: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._chart_workflow.completed_candle(candle)

    @Slot(object, int)
    def _chart_in_progress_candle(self, candle: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._chart_workflow.in_progress_candle(candle)

    @Slot(object)
    def _chart_view_requested(self, value: object) -> None:
        if not isinstance(value, ChartViewSelection):
            return
        try:
            snapshot = self._lifecycle_workflow.snapshot
        except RuntimeError:
            return
        session = snapshot.session
        self._chart_workflow.select_view(value)
        if self._select_chart_view is not None:
            self._select_chart_view(session, value)

    @Slot(object, int)
    def _runtime_completed_candle(self, candle: object, generation: int) -> None:
        relay = self._runtime_snapshots
        if relay is None or not relay.is_current(generation):
            return
        self._chart_workflow.completed_candle(candle)

    @Slot(object, int)
    def _runtime_in_progress_candle(self, candle: object, generation: int) -> None:
        relay = self._runtime_snapshots
        if relay is None or not relay.is_current(generation):
            return
        self._chart_workflow.in_progress_candle(candle)

    @Slot(str, str)
    def _show_validation_error(self, field: str, message: str) -> None:
        self.setup.show_field_error(field, message)
        self._start_after_create = False
        self._pending_validation_field = field

    def _show_unavailable(self, message: str) -> None:
        self._pending_validation_field = None
        self._start_after_create = False
        self.workspace.show_unavailable(message)

    @Slot()
    def _show_setup(self) -> None:
        self.workspace.show_setup()

    @Slot()
    def _start_trade_history_once(self) -> None:
        if self._history_started:
            return
        self._history_started = True
        self._history_workflow.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._close_sequence_completed:
            event.ignore()
            if not self._close_sequence_started:
                self._close_sequence_started = True
                self._close_workflows()
                if self._shutdown_runtime is None:
                    self._start_worker_drain()
                else:
                    task = BackgroundTask(self._shutdown_runtime)
                    task.signals.finished.connect(self._runtime_shutdown_finished)
                    self._shutdown_task = task
                    self._runtime_shutdown_pool.start(task)
            return
        super().closeEvent(event)

    def _close_workflows(self) -> None:
        if self._chart_fact_relay is not None:
            self._chart_fact_relay.invalidate()
        self._workflow.close()
        self._lifecycle_workflow.close()
        self._history_workflow.close()
        self._chart_workflow.close()

    @Slot()
    def _runtime_shutdown_finished(self) -> None:
        self._shutdown_task = None
        self._start_worker_drain()

    def _start_worker_drain(self) -> None:
        task = BackgroundTask(
            lambda: self._thread_pool.waitForDone(WORKER_SHUTDOWN_TIMEOUT_MS)
        )
        task.signals.finished.connect(self._close_sequence_finished)
        self._shutdown_task = task
        self._runtime_shutdown_pool.start(task)

    @Slot()
    def _close_sequence_finished(self) -> None:
        self._close_sequence_completed = True
        self._shutdown_task = None
        self.close()

    def _wire_trade_history(self) -> None:
        self.trade_history.apply_filters_requested.connect(
            self._history_workflow.apply_filters
        )
        self.trade_history.reset_requested.connect(self._history_workflow.reset_filters)
        self.trade_history.history_tab_changed.connect(
            self._history_workflow.select_tab
        )
        self.trade_history.baskets_retry_requested.connect(
            self._history_workflow.retry_baskets
        )
        self.trade_history.fills_retry_requested.connect(
            self._history_workflow.retry_fills
        )
        self._history_workflow.baskets_loading.connect(
            self.trade_history.set_baskets_loading
        )
        self._history_workflow.baskets_ready.connect(self.trade_history.show_baskets)
        self._history_workflow.baskets_empty.connect(
            self.trade_history.show_baskets_empty
        )
        self._history_workflow.baskets_unavailable.connect(
            self.trade_history.show_baskets_unavailable
        )
        self._history_workflow.filter_invalid.connect(
            self.trade_history.show_filter_error
        )
        self._history_workflow.trades_ready.connect(self.trade_history.show_trades)
        self._history_workflow.trades_loading.connect(
            self.trade_history.set_trades_loading
        )
        self._history_workflow.trades_empty.connect(
            self.trade_history.show_trades_empty
        )
        self._history_workflow.trades_unavailable.connect(
            self.trade_history.show_trades_unavailable
        )


async def _unused_chart_loader(
    session: ConfiguredPaperSession, chart_range: ChartRange
) -> ChartSnapshot:
    return ChartSnapshot(
        session=session,
        chart_range=chart_range,
        observed_at_utc=chart_range.end,
        candles=(),
        fills=(),
        state=ChartReadState.UNAVAILABLE,
        message="Chart is unavailable",
    )
