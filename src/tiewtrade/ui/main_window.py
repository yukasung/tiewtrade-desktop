import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox

from tiewtrade.application.bot_control import BotControlAction, BotControlSnapshot
from tiewtrade.application.chart_data import (
    ChartCandle,
    ChartFeedState,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
    PaperSessionValidationError,
    validate_paper_session,
)
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BotRuntimeState,
    DataFreshness,
)
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.bot_lifecycle_workflow import (
    BotLifecycleWorkflow,
    LifecycleAction,
    RuntimeSnapshotRelay,
)
from tiewtrade.ui.chart_workflow import (
    PRICE_SILENCE_CHECK_MILLISECONDS,
    ChartFactRelay,
    ChartWorkflow,
    LoadChart,
    LoadChartFills,
    RefreshChart,
)
from tiewtrade.ui.connection_notice import (
    ConnectionNotice,
    ConnectionNoticeDialog,
)
from tiewtrade.ui.open_position_workflow import (
    OpenPositionWorkflow,
    ReadOpenBasket,
)
from tiewtrade.ui.position_dialogs import (
    ask_about_cancelling_cooldown,
    ask_about_selling_one_entry,
)
from tiewtrade.ui.session_workflow import (
    CreateSession,
    LoadActiveSession,
    SessionWorkflow,
)
from tiewtrade.ui.start_decision import StartCourse, course_before_start
from tiewtrade.ui.start_dialogs import (
    PositionAtTarget,
    PositionOnStart,
    ask_about_a_position_at_its_target,
    ask_about_the_open_position,
)
from tiewtrade.ui.take_profit_confirmation import UNREADABLE_POSITION_MESSAGE
from tiewtrade.ui.trade_history_workflow import (
    ListBasketHistory,
    ListTradeFills,
    TradeHistoryWorkflow,
)
from tiewtrade.ui.trading_workspace import TradingWorkspace

_LOGGER = logging.getLogger("tiewtrade.ui.main_window")

WORKER_SHUTDOWN_TIMEOUT_MS = 5_000

# A question put to the trader, answered by calling the confirmation it is
# handed. The same shape the Stop confirmation takes, so both can be given a
# plain callable in a test rather than a dialog to click.
TakeProfitConfirmation = Callable[[str, Callable[[], None]], None]

# Told to the trader, not asked of them: the Bot has already stopped by the
# time this is called. A plain callable, so a test can read what was said.
BlockedNotice = Callable[[str], None]

# What the one system notice says, worst first. It carries only faults the
# reader has to act on; a connection being retried is told in a dialog, which
# a reader looking anywhere else on the screen still meets.
CHART_FEED_STOPPED_NOTICE = "Chart feed stopped — prices are not current"

# How long a connection has to stay away before the trader is taken away from
# what they were doing. The runtime retries for as long as it takes, climbing
# from a second to a thirty-second ceiling, so a drop shorter than this is one
# it was always going to ride out on its own.
CONNECTION_NOTICE_DELAY_MILLISECONDS = 30_000


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
    # One Entry of the open Basket, sold at the price the market is at. True
    # where it sold, False where there was no such Entry left to sell.
    close_entry: Callable[[ConfiguredPaperSession, int], bool] | None = None
    # Lifts the Cooldown blocking the open Basket's next Entry. True where
    # one was blocking it and got lifted, False otherwise.
    cancel_cooldown: Callable[[ConfiguredPaperSession], bool] | None = None
    has_open_position: Callable[[ConfiguredPaperSession], bool] | None = None
    retire_session: Callable[[ConfiguredPaperSession], None] | None = None
    read_open_basket: ReadOpenBasket | None = None


@dataclass(frozen=True, slots=True)
class ChartSources:
    """Where the chart's candles and fills come from, and what keeps them live."""

    load: LoadChart | None = None
    refresh: RefreshChart | None = None
    load_fills: LoadChartFills | None = None
    facts: ChartFactRelay | None = None
    start_feed: Callable[[ConfiguredPaperSession], None] | None = None
    stop_feed: Callable[[], None] | None = None
    select_view: Callable[[ConfiguredPaperSession, ChartViewSelection], None] | None = (
        None
    )
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
        take_profit_confirmation: TakeProfitConfirmation | None = None,
        blocked_notice: BlockedNotice | None = None,
        connection_notice: ConnectionNotice | None = None,
        connection_notice_delay_ms: int = CONNECTION_NOTICE_DELAY_MILLISECONDS,
    ) -> None:
        super().__init__()
        self._take_profit_confirmation = (
            take_profit_confirmation or self._open_take_profit_confirmation
        )
        self._blocked_notice = blocked_notice or self._open_blocked_notice
        self._connection_notice = connection_notice or ConnectionNoticeDialog(self)
        self._connection_lost = False
        self._last_bot_state: BotRuntimeState | None = None
        self._take_profit_confirmation_pending = False
        self._take_profit_confirmation_dialog: QMessageBox | None = None
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._load_chart = chart.load
        self._close_position = bot.close_position
        self._close_entry = bot.close_entry
        self._close_entry_task: BackgroundTask | None = None
        self._cancel_cooldown = bot.cancel_cooldown
        self._cancel_cooldown_task: BackgroundTask | None = None
        self._waiting_on_a_reading: list[Callable[[], None]] = []
        self._has_open_position = bot.has_open_position
        self._clock = chart.clock
        self._retire_session = bot.retire_session
        self._stop_for_replacement = bot.stop_for_replacement
        self._validate_session = validate_session or validate_paper_session
        self._shutdown_runtime = bot.shutdown_runtime
        self._chart_fact_relay = chart.facts
        self._start_chart_feed = chart.start_feed
        self._select_chart_view = chart.select_view
        self._stop_chart_feed = chart.stop_feed
        self._chart_feed_state: ChartFeedState | None = None
        self._bot_stopped_reason = ""
        self._bot_reconnecting = False
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
        self._workflow.workspace_changed.connect(self._show_workspace_snapshot)
        self._workflow.setup_required.connect(self._show_setup)
        self._workflow.session_ready.connect(self._show_session)
        self._workflow.validation_failed.connect(self._show_validation_error)
        self._workflow.unavailable.connect(self._show_unavailable)
        self.setup.create_requested.connect(self._create_requested)
        self.unavailable_retry_button.clicked.connect(self._retry_unavailable)

        self._open_position = OpenPositionWorkflow(
            read_open_basket=bot.read_open_basket,
            thread_pool=self._thread_pool,
            clock=self._clock,
            parent=self,
        )
        self._open_position.snapshot_changed.connect(
            self.workspace.position_basket.show_snapshot
        )
        self.workspace.position_basket.close_entry_requested.connect(
            self._close_entry_requested
        )
        self.workspace.position_basket.cancel_cooldown_requested.connect(
            self._cancel_cooldown_requested
        )
        self._open_position.reading_landed.connect(self._position_reading_landed)

        self._lifecycle_workflow = BotLifecycleWorkflow(
            start_bot=bot.start,
            stop_bot=bot.stop,
            initialize_bot=bot.initialize,
            runtime_snapshots=bot.runtime_snapshots,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._lifecycle_workflow.snapshot_changed.connect(self._bot_control_changed)
        self.workspace.start_bot_requested.connect(self._start_requested)
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
        self._chart_workflow.following_latest_changed.connect(
            self.workspace.chart.set_following_latest
        )
        self._chart_workflow.feed_state_changed.connect(self._show_chart_feed_state)
        # Prices stopping is not an event anything can send; it is the
        # absence of one, so somebody has to keep asking.
        self._price_silence_timer = QTimer(self)
        self._price_silence_timer.setInterval(PRICE_SILENCE_CHECK_MILLISECONDS)
        self._price_silence_timer.timeout.connect(
            self._chart_workflow.check_price_silence
        )
        self._price_silence_timer.start()
        # A drop worth telling is one that lasts, so the telling waits.
        self._connection_notice_timer = QTimer(self)
        self._connection_notice_timer.setSingleShot(True)
        self._connection_notice_timer.setInterval(connection_notice_delay_ms)
        self._connection_notice_timer.timeout.connect(self._announce_connection_lost)
        self.workspace.chart.range_requested.connect(self._chart_workflow.load_range)
        self.workspace.chart.latest_requested.connect(
            self._chart_workflow.follow_latest
        )
        self.workspace.chart.retry_requested.connect(self._chart_retry_requested)
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
            chart.facts.feed_state_ready.connect(
                self._chart_feed_state_received,
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
        # The copy on screen is only as new as the last time something asked
        # the database — a Session left running elsewhere goes on trading past
        # it. A question about a Position is put to the Position that is there
        # now, not to the one the window happens to be holding.
        self._once_the_position_is_read(
            self._replacement_session, self._take_the_start_course
        )

    def _take_the_start_course(self) -> None:
        if self._replacement_values is None or self._replacement_session is None:
            return
        course = course_before_start(
            position_open=self._position_is_open(),
            sells_on_start=self._sells_on_start_message() is not None,
            leaves_entries_holding=self._open_position.leaves_entries_holding(),
        )
        if course is StartCourse.ASK_ABOUT_THE_SALE:
            self._act_on_a_position_at_its_target()
            return
        if course is StartCourse.ASK_ABOUT_THE_POSITION:
            self._act_on_the_open_position()
            return
        self._replace_session()

    def _once_the_position_is_read(
        self,
        session: ConfiguredPaperSession | None,
        then: Callable[[], None],
    ) -> None:
        """Read the Basket again, and do this with the answer that comes back.

        A Start puts an irreversible question, and it once put one about an
        Entry that had already sold: the window had read the Basket when it
        opened and nothing had asked since. No Session to read for is nothing
        to wait on.
        """
        self._waiting_on_a_reading.append(then)
        if session is None:
            self._position_reading_landed()
            return
        self._open_position.read_for(session)

    @Slot()
    def _position_reading_landed(self) -> None:
        """Whatever was waiting for a current reading now has one."""
        waiting = self._waiting_on_a_reading
        self._waiting_on_a_reading = []
        for then in waiting:
            then()

    def _position_is_open(self) -> bool:
        """Whether anything is held — by the database, or by a running Bot."""
        if self._replacement_session is None:
            return False
        persisted = (
            self._has_open_position(self._replacement_session)
            if self._has_open_position is not None
            else False
        )
        running = self._lifecycle_workflow.snapshot.workspace.basket is not None
        return persisted or running

    def _act_on_a_position_at_its_target(self) -> None:
        reached = self._position_at_target_message()
        if reached is None:
            return
        answer = ask_about_a_position_at_its_target(self, reached)
        if answer is PositionAtTarget.CLOSE_AT_MARKET:
            self._replace_session()
            return
        # Nothing was decided, so the form's edits describe a Session that is
        # not being made.
        self._replacement_values = None

    def _act_on_the_open_position(self) -> None:
        answer = ask_about_the_open_position(
            self,
            self._position_held_message() or UNREADABLE_POSITION_MESSAGE,
            # Said only where part of the Position has reached a target, which
            # is what makes running the Session on a sale of its own.
            self._position_at_target_message(),
        )
        if answer is PositionOnStart.START_ON_IT:
            self._resume_replacement_session()
            return
        if answer is PositionOnStart.CLOSE_AT_MARKET:
            self._replace_session()
            return
        # Nothing was decided, so the form's edits describe a Session that is
        # not being made.
        self._replacement_values = None

    def _resume_replacement_session(self) -> None:
        """Run the Session that is already there on, position and all.

        Nothing is created and nothing is closed, so the form's edits describe
        a Session that is not being made: the values go back to the ones the
        running Session actually holds.
        """
        session = self._replacement_session
        if session is None:
            return
        self._replacement_values = None
        available = self._lifecycle_workflow.snapshot.available_actions
        if BotControlAction.START not in available:
            self.setup.show_field_error(
                "capital_per_entry",
                "The existing Paper Session cannot be started again. "
                "Close its positions to start a new one.",
            )
            return
        self.setup.set_values_from_session(session)
        # Started without asking again: a Position at its target never reaches
        # here — that course closes at the market price or does nothing — so
        # there is no sale left for a question to be about.
        self._start_now()

    @Slot()
    def _start_requested(self) -> None:
        """Start the Bot, asking first about any Position it would run on.

        A Position left open goes on being marked while nothing runs, so the
        price can reach an Entry's target with no Bot to act on it. The first
        live tick after a Start sells every such Entry at once, at the target
        it was given rather than at the price on screen — and there is no way
        back from a Fill. Short of a target it sells nothing, but it is still
        this press that hands an open Position back to a Bot, so it is said
        here too. The routes that put no question of their own come through
        here, which is where this one is put — to the Position that is there
        now, which is why the Basket is read again before it is asked about.
        """
        if self._take_profit_confirmation_pending:
            return
        try:
            session = self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            session = None
        self._once_the_position_is_read(session, self._ask_before_starting)

    def _ask_before_starting(self) -> None:
        """Put the question the reading turned out to be worth, or none."""
        message = self._sells_on_start_message() or self._position_held_message()
        if message is None:
            self._start_now()
            return
        if self._take_profit_confirmation_pending:
            return
        self._take_profit_confirmation_pending = True
        self._take_profit_confirmation(message, self._confirm_start)

    def _start_now(self) -> None:
        """The one line that starts the Bot, whichever question preceded it."""
        self._lifecycle_workflow.start_bot()

    def _sells_on_start_message(self) -> str | None:
        """What a Start would sell now, asked of the Position that is held.

        A Futures Session never takes a Basket back, so it answers the same
        way as having none: there is nothing to put a question about.
        """
        return self._said_or_asked_anyway(self._open_position.sells_on_start_message())

    def _position_at_target_message(self) -> str | None:
        """What the held Position has reached, in the words the dialog puts it.

        Read from the same Basket and price as _sells_on_start_message, which
        is what decided a question was worth putting at all.
        """
        return self._said_or_asked_anyway(
            self._open_position.position_at_target_message()
        )

    def _position_held_message(self) -> str | None:
        """What the Position stands at, in the words the dialog puts it in.

        Read from the same Basket and price as the other two, which is what
        decided a question was worth putting at all.
        """
        return self._said_or_asked_anyway(self._open_position.position_held_message())

    def _said_or_asked_anyway(self, message: str | None) -> str | None:
        """The reading itself, or the reason there is none to be had.

        Silence has two meanings — nothing is at its target, and nothing can
        be told yet — and only one of them makes it safe to start without
        asking. A Basket still being read, or one with no price yet, took the
        question away and sold a Position at a target the price had left.
        """
        if message is not None:
            return message
        if self._open_position.what_a_start_sells_is_unknown():
            return UNREADABLE_POSITION_MESSAGE
        return None

    def _confirm_start(self) -> None:
        self._take_profit_confirmation_pending = False
        self._start_now()

    def _open_take_profit_confirmation(
        self, message: str, confirm: Callable[[], None]
    ) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Open Position")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText("Start the Bot on the Position it holds?")
        dialog.setInformativeText(message)
        start_button = dialog.addButton("Start Bot", QMessageBox.ButtonRole.AcceptRole)
        cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        # The confirmed answer is the one that cannot be taken back, so the
        # key an impatient hand hits is the one that changes nothing.
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)

        def finish(_result: int) -> None:
            if dialog.clickedButton() is start_button:
                confirm()
            else:
                self._take_profit_confirmation_pending = False
            dialog.deleteLater()

        dialog.finished.connect(finish)
        self._take_profit_confirmation_dialog = dialog
        dialog.open()

    def _replace_session(self) -> None:
        session = self._replacement_session
        values = self._replacement_values
        retire_session = self._retire_session
        if session is None or values is None:
            return
        if retire_session is None:
            self.setup.show_field_error(
                "capital_per_entry",
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
        # The Position this was holding has just been closed, so the Start
        # that follows must not ask about a Basket that has been sold.
        self._open_position.forget()

    @Slot(object)
    def _replacement_failed(self, _error: object) -> None:
        message = "Could not stop, close, or retire the existing Paper Session. "
        if isinstance(_error, RuntimeError) and str(_error) == (
            "A fresh market price is unavailable"
        ):
            message = (
                "Could not reach live market data to price the close. "
                "Check your internet connection, then try again."
            )
        elif isinstance(_error, RuntimeError) and str(_error) == (
            "Paper Bot must stop before it can be replaced"
        ):
            message = (
                "Paper Bot is still stopping. Wait for it to stop, then try again."
            )
        elif str(_error) == "Active Paper Session cannot be retired":
            message = (
                "The existing Paper Session is still running somewhere. "
                "Stop it, or restart the application."
            )
        self.setup.show_field_error(
            "capital_per_entry",
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
        self._open_position.read_for(session)
        if self._load_chart is not None:
            self._chart_workflow.start(session)
        if self._start_chart_feed is not None:
            self._start_chart_feed(session)

    @Slot(object)
    def _show_workspace_snapshot(self, value: object) -> None:
        """The Runtime's view of the tabs, then the Basket that outlives it.

        A stopped Runtime holds no Basket, so its snapshot paints the Position
        tab empty. The Basket the window read from the database goes back
        straight after: a tab reading "No open Position" over an open one is
        the single answer that is never true.
        """
        self.workspace.show_workspace_snapshot(value)
        self._open_position.reassert()

    @Slot(object)
    def _bot_control_changed(self, value: object) -> None:
        if not isinstance(value, BotControlSnapshot):
            return
        previous_state = self._last_bot_state
        self._last_bot_state = value.state
        self._read_bot_notice(value, previous_state)
        self.workspace.show_bot_control_snapshot(value)
        self._chart_workflow.runtime_snapshot(value)
        if value.state is BotRuntimeState.RUNNING:
            self._open_position.held_by_runtime()
        elif value.state is BotRuntimeState.STOPPED:
            # Whatever the Bot left open is the tab's to show again.
            self._open_position.read_for(value.session)
        # The snapshot just shown carries no Basket while the Bot is stopped,
        # so it painted the tab empty. Put the held one back — after the
        # branch above, so a running Runtime keeps the tab to itself.
        self._open_position.reassert()
        if (
            self._pending_start_session == value.session
            and value.state is BotRuntimeState.CONFIGURED
            and BotControlAction.START in value.available_actions
        ):
            self._pending_start_session = None
            self._start_requested()
            return
        if value.state is BotRuntimeState.CONFIGURED:
            self._show_editable_setup(value)
            return
        if self._return_to_setup_after_stop and value.state is BotRuntimeState.STOPPED:
            self._return_to_setup_after_stop = False
            self._show_editable_setup(value)
            return
        if value.state is BotRuntimeState.BLOCKED:
            self._announce_blocked_runtime(value, previous_state)
            self._show_editable_setup(value)

    @Slot()
    def _stop_requested(self) -> None:
        self._return_to_setup_after_stop = True
        self._lifecycle_workflow.stop_bot()

    def _read_bot_notice(
        self,
        snapshot: BotControlSnapshot,
        previous_state: BotRuntimeState | None,
    ) -> None:
        """What the Bot has to say on the one line the system speaks on.

        A Bot waiting on data it cannot reach reads as a Bot that is trading,
        and a Bot that stopped while the reader was elsewhere leaves nothing
        behind once its dialog is closed.
        """
        header = snapshot.workspace.header
        if snapshot.state is BotRuntimeState.RUNNING:
            self._bot_stopped_reason = ""
            self._bot_reconnecting = (
                header is not None and header.data_freshness is DataFreshness.STALE
            )
        elif (
            snapshot.state is BotRuntimeState.BLOCKED
            and previous_state is BotRuntimeState.RUNNING
        ):
            self._bot_stopped_reason = snapshot.blocked_reason or ""
            self._bot_reconnecting = False
        else:
            self._bot_stopped_reason = ""
            self._bot_reconnecting = False
        self._show_system_notice()
        self._update_connection_notice()

    def _announce_blocked_runtime(
        self,
        snapshot: BotControlSnapshot,
        previous_state: BotRuntimeState | None,
    ) -> None:
        """Say why a running Bot stopped; a Bot that never ran needs no telling.

        A start that fails answers on the form the trader is looking at. This
        is the other case: the Bot was running, the trader was elsewhere, and
        the workspace is about to go back to the form on its own.
        """
        if previous_state is not BotRuntimeState.RUNNING:
            return
        reason = snapshot.blocked_reason
        if reason is None:
            return
        self._blocked_notice(reason)

    def _open_blocked_notice(self, reason: str) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Paper Bot Stopped")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(reason)
        dialog.setInformativeText(
            "The Session is open on the setup form. Start the Bot again when "
            "the connection is back."
        )
        dialog.addButton(QMessageBox.StandardButton.Ok)
        dialog.open()

    def _show_editable_setup(self, snapshot: BotControlSnapshot) -> None:
        self._replacement_session = snapshot.session
        self.setup.set_values_from_session(snapshot.session)
        self.workspace.show_setup()

    def _mark_chart_candle(self, candle: object) -> None:
        """Keep the newest price the chart has, which a resting Basket marks on."""
        if not isinstance(candle, ChartCandle):
            return
        self._open_position.mark_price(candle)

    @Slot(object, int)
    def _chart_completed_candle(self, candle: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._mark_chart_candle(candle)
        self._chart_workflow.completed_candle(candle)

    @Slot(object, int)
    def _chart_feed_state_received(self, state: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._chart_workflow.feed_state(state)

    @Slot(object)
    def _show_chart_feed_state(self, state: object) -> None:
        if not isinstance(state, ChartFeedState):
            return
        self._chart_feed_state = state
        self.workspace.chart.show_feed_state(state)
        self._show_system_notice()
        self._update_connection_notice()

    def _show_system_notice(self) -> None:
        self.workspace.chart.show_system_notice(self._system_notice_text())

    def _update_connection_notice(self) -> None:
        """Raise the connection notice on a drop that lasts, drop it on return.

        Only the crossings matter. A drop keeps producing snapshots for as
        long as it lasts, and a countdown restarted by each of them would
        never reach the end of its wait.
        """
        lost = not self._bot_stopped_reason and (
            self._bot_reconnecting
            or self._chart_feed_state is ChartFeedState.RECONNECTING
        )
        if lost == self._connection_lost:
            return
        self._connection_lost = lost
        if lost:
            self._connection_notice_timer.start()
            return
        self._connection_notice_timer.stop()
        self._connection_notice.hide()

    @Slot()
    def _announce_connection_lost(self) -> None:
        self._connection_notice.show()

    def _system_notice_text(self) -> str:
        if self._bot_stopped_reason:
            return self._bot_stopped_reason
        if self._chart_feed_state is ChartFeedState.STOPPED:
            return CHART_FEED_STOPPED_NOTICE
        return ""

    @Slot()
    def _chart_retry_requested(self) -> None:
        """Read the history again, and put a feed back under it if it is gone.

        A chart whose feed has stopped is two faults at once, and reloading
        the history alone answers only the half the reader can see: the
        candles come back and then stop arriving again immediately.
        """
        self._chart_workflow.retry()
        if (
            self._chart_feed_state is not ChartFeedState.STOPPED
            or self._start_chart_feed is None
        ):
            return
        try:
            session = self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            return
        self._start_chart_feed(session)

    @Slot(object, int)
    def _chart_in_progress_candle(self, candle: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._mark_chart_candle(candle)
        self._chart_workflow.in_progress_candle(candle)

    @Slot(int)
    def _close_entry_requested(self, entry_number: int) -> None:
        """Sell one Entry, once the trader has said so in as many words.

        Which of the two is holding the Basket is not this window's to know:
        the action is asked, and it goes to the Runtime that owns it or to the
        database a stopped Bot left it in.
        """
        close_entry = self._close_entry
        if close_entry is None or self._close_entry_task is not None:
            return
        try:
            session = self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            return
        if not ask_about_selling_one_entry(self, entry_number):
            return
        self.workspace.position_basket.set_closing_enabled(False)
        task = BackgroundTask(lambda: close_entry(session, entry_number))
        task.signals.succeeded.connect(self._entry_closed)
        task.signals.failed.connect(self._entry_close_failed)
        task.signals.finished.connect(self._entry_close_finished)
        self._close_entry_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _entry_closed(self, _result: object) -> None:
        """Read the Basket back, which is how a stopped Bot's tab hears of it.

        A running Runtime publishes the sale itself; a stopped one leaves the
        database as the only witness, and the tab is showing what it last read
        from it — an Entry that has just gone.
        """
        try:
            session = self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            return
        if self._lifecycle_workflow.snapshot.state is not BotRuntimeState.RUNNING:
            self._open_position.read_for(session)

    @Slot(object)
    def _entry_close_failed(self, error: object) -> None:
        if isinstance(error, BaseException):
            _LOGGER.exception("Could not sell that Entry", exc_info=error)
        self.workspace.position_basket.show_close_error(
            "Could not sell that Entry. Check your connection, then try again."
        )

    @Slot()
    def _entry_close_finished(self) -> None:
        self._close_entry_task = None
        self.workspace.position_basket.set_closing_enabled(True)

    @Slot()
    def _cancel_cooldown_requested(self) -> None:
        """Lift a Cooldown, once the trader has said so in as many words.

        Which of the two is holding the Basket is not this window's to know:
        the action is asked, and it goes to the Runtime that owns it or to the
        database a stopped Bot left it in.
        """
        cancel_cooldown = self._cancel_cooldown
        if cancel_cooldown is None or self._cancel_cooldown_task is not None:
            return
        basket = self.workspace.position_basket.basket
        if basket is None or basket.entry_gate is not BasketEntryGate.COOLDOWN:
            return
        try:
            session = self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            return
        if not ask_about_cancelling_cooldown(self):
            return
        self.workspace.position_basket.set_cancelling_enabled(False)
        task = BackgroundTask(lambda: cancel_cooldown(session))
        task.signals.succeeded.connect(self._cooldown_cancelled)
        task.signals.failed.connect(self._cancel_cooldown_failed)
        task.signals.finished.connect(self._cancel_cooldown_finished)
        self._cancel_cooldown_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _cooldown_cancelled(self, _result: object) -> None:
        """Read the Basket back, which is how a stopped Bot's tab hears of it.

        A running Runtime publishes the waiver itself; a stopped one leaves
        the database as the only witness, and the tab is showing what it last
        read from it — a gate that has just opened.
        """
        try:
            session = self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            return
        if self._lifecycle_workflow.snapshot.state is not BotRuntimeState.RUNNING:
            self._open_position.read_for(session)

    @Slot(object)
    def _cancel_cooldown_failed(self, error: object) -> None:
        if isinstance(error, BaseException):
            _LOGGER.exception("Could not waive the Cooldown", exc_info=error)
        self.workspace.position_basket.show_cancel_error(
            "Could not waive the Cooldown. Check your connection, then try again."
        )

    @Slot()
    def _cancel_cooldown_finished(self) -> None:
        self._cancel_cooldown_task = None
        self.workspace.position_basket.set_cancelling_enabled(True)

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
        self._connection_notice_timer.stop()
        self._connection_notice.hide()
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
