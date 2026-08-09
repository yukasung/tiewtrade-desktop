from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow

from tiewtrade.application.bot_control import BotControlAction, BotControlSnapshot
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
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
    RefreshChart,
)
from tiewtrade.ui.session_workflow import (
    CreateSession,
    LoadActiveSession,
    SessionWorkflow,
)
from tiewtrade.ui.trade_history_workflow import (
    ListBaskets,
    ListFills,
    TradeHistoryWorkflow,
)
from tiewtrade.ui.trading_workspace import TradingWorkspace

WORKER_SHUTDOWN_TIMEOUT_MS = 5_000


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        create_session: CreateSession,
        load_active: LoadActiveSession,
        list_baskets: ListBaskets,
        list_fills: ListFills,
        start_bot: LifecycleAction | None = None,
        stop_bot: LifecycleAction | None = None,
        recover_bot: LifecycleAction | None = None,
        close_position: Callable[[ConfiguredPaperSession], None] | None = None,
        retire_session: Callable[[ConfiguredPaperSession], None] | None = None,
        new_session_available: Callable[[ConfiguredPaperSession], bool] | None = None,
        initialize_bot: LifecycleAction | None = None,
        runtime_snapshots: RuntimeSnapshotRelay | None = None,
        shutdown_runtime: Callable[[], None] | None = None,
        load_chart: LoadChart | None = None,
        refresh_chart: RefreshChart | None = None,
        chart_fact_relay: ChartFactRelay | None = None,
        start_chart_feed: Callable[[ConfiguredPaperSession], None] | None = None,
        stop_chart_feed: Callable[[], None] | None = None,
        chart_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._load_chart = load_chart
        self._close_position = close_position
        self._retire_session = retire_session
        self._shutdown_runtime = shutdown_runtime
        self._chart_fact_relay = chart_fact_relay
        self._start_chart_feed = start_chart_feed
        self._stop_chart_feed = stop_chart_feed
        self._close_sequence_started = False
        self._close_sequence_completed = False
        self._shutdown_task: BackgroundTask | None = None
        self._new_session_task: BackgroundTask | None = None
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
        self._new_session_retry_pending = False

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
            start_bot=start_bot,
            stop_bot=stop_bot,
            recover=recover_bot,
            new_session_available=new_session_available,
            initialize_bot=initialize_bot,
            runtime_snapshots=runtime_snapshots,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._lifecycle_workflow.snapshot_changed.connect(self._bot_control_changed)
        self._lifecycle_workflow.notifications_changed.connect(
            self.workspace.show_notifications
        )
        self.workspace.start_bot_requested.connect(self._lifecycle_workflow.start_bot)
        self.workspace.stop_bot_requested.connect(self._stop_requested)
        self.workspace.recover_requested.connect(self._lifecycle_workflow.recover)
        self.workspace.new_session_requested.connect(self._new_session_requested)

        self._chart_workflow = ChartWorkflow(
            load_chart=load_chart or _unused_chart_loader,
            refresh_chart=refresh_chart,
            thread_pool=self._thread_pool,
            clock=chart_clock,
            parent=self,
        )
        self._chart_workflow.snapshot_changed.connect(
            self.workspace.chart.show_snapshot
        )
        self.workspace.chart.range_requested.connect(self._chart_workflow.load_range)
        self.workspace.chart.retry_requested.connect(self._chart_workflow.retry)
        if chart_fact_relay is not None:
            chart_fact_relay.completed_candle_ready.connect(
                self._chart_completed_candle,
                Qt.ConnectionType.QueuedConnection,
            )
            chart_fact_relay.in_progress_candle_ready.connect(
                self._chart_in_progress_candle,
                Qt.ConnectionType.QueuedConnection,
            )
        self._runtime_snapshots = runtime_snapshots
        if chart_fact_relay is None and runtime_snapshots is not None:
            runtime_snapshots.completed_candle_ready.connect(
                self._runtime_completed_candle,
                Qt.ConnectionType.QueuedConnection,
            )
            runtime_snapshots.in_progress_candle_ready.connect(
                self._runtime_in_progress_candle,
                Qt.ConnectionType.QueuedConnection,
            )

        self._history_started = False
        self._history_workflow = TradeHistoryWorkflow(
            list_baskets=list_baskets,
            list_fills=list_fills,
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
        self._start_after_create = True
        self.setup.clear_errors()
        self._workflow.create(raw_values)

    @Slot()
    def _new_session_requested(self) -> None:
        retire_session = self._retire_session
        if retire_session is None or self._new_session_task is not None:
            return
        snapshot = self._lifecycle_workflow.snapshot

        def close_and_retire() -> None:
            if snapshot.workspace.basket is not None:
                close_position = self._close_position
                if close_position is None:
                    raise RuntimeError("Paper position close is unavailable")
                close_position(snapshot.session)
            retire_session(snapshot.session)

        task = BackgroundTask(close_and_retire)
        task.signals.succeeded.connect(self._new_session_completed)
        task.signals.failed.connect(self._new_session_failed)
        task.signals.finished.connect(self._new_session_finished)
        self._new_session_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _new_session_completed(self, _result: object) -> None:
        self._new_session_retry_pending = False
        self._workflow.start()

    @Slot(object)
    def _new_session_failed(self, _error: object) -> None:
        self._new_session_retry_pending = True
        self._show_unavailable(
            "Could not close the Paper position or retire the Session. Try again."
        )

    @Slot()
    def _new_session_finished(self) -> None:
        self._new_session_task = None

    @Slot()
    def _retry_unavailable(self) -> None:
        if self._new_session_retry_pending:
            self._new_session_requested()
            return
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
        if (
            self._pending_start_session == value.session
            and value.state is BotRuntimeState.CONFIGURED
            and BotControlAction.START in value.available_actions
        ):
            self._pending_start_session = None
            self._lifecycle_workflow.start_bot()
            return
        if self._return_to_setup_after_stop and value.state is BotRuntimeState.STOPPED:
            self._return_to_setup_after_stop = False
            self._show_editable_setup(value)

    @Slot()
    def _stop_requested(self) -> None:
        self._return_to_setup_after_stop = True
        self._lifecycle_workflow.stop_bot()

    def _show_editable_setup(self, snapshot: BotControlSnapshot) -> None:
        if snapshot.workspace.basket is not None or snapshot.workspace.orders:
            return
        retire_session = self._retire_session
        if retire_session is None:
            return
        try:
            retire_session(snapshot.session)
        except Exception:
            self._show_unavailable("Session storage is unavailable")
            return
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
        self.trade_history.page_requested.connect(self._history_workflow.go_to_page)
        self.trade_history.basket_selected.connect(self._history_workflow.select_basket)
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
        self._history_workflow.fills_loading.connect(
            self.trade_history.set_fills_loading
        )
        self._history_workflow.fills_ready.connect(self.trade_history.show_fills)
        self._history_workflow.fills_empty.connect(self.trade_history.show_fills_empty)
        self._history_workflow.fills_unavailable.connect(
            self.trade_history.show_fills_unavailable
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
