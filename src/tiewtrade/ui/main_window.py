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
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    PositionBasketTabSnapshot,
    TradingWorkspaceSnapshot,
)
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
from tiewtrade.ui.connection_notice import ConnectionNotice
from tiewtrade.ui.guarded_action import GuardedAction
from tiewtrade.ui.open_position_workflow import (
    OpenPositionWorkflow,
    ReadOpenBasket,
)
from tiewtrade.ui.position_dialogs import (
    ask_about_cancelling_cooldown,
    ask_about_closing_all_at_market,
    ask_about_selling_one_entry,
)
from tiewtrade.ui.session_workflow import (
    CreateSession,
    LoadActiveSession,
    SessionWorkflow,
)
from tiewtrade.ui.settings_dialogs import ask_about_clearing_database
from tiewtrade.ui.settings_panel import LineNotificationsReading
from tiewtrade.ui.start_decision import StartCourse, course_before_start
from tiewtrade.ui.start_dialogs import (
    PositionAtTarget,
    PositionOnStart,
    ask_about_a_position_at_its_target,
    ask_about_the_open_position,
)
from tiewtrade.ui.take_profit_confirmation import UNREADABLE_POSITION_MESSAGE
from tiewtrade.ui.total_pnl_workflow import (
    ReadTotalRealizedPnl,
    TotalPnlWorkflow,
)
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

# The database may be cleared whenever the Bot is not actually trading or
# moving between states. Stopped, Blocked, and a freshly Configured Session
# all resolve through the same editable Setup form, whose only way onward
# retires whatever Session was there and creates a fresh one — so a cleared
# database never leaves that form pointed at a row that just went.
_CLEAR_DATABASE_BLOCKED_STATES = frozenset(
    {
        BotRuntimeState.STARTING,
        BotRuntimeState.RUNNING,
        BotRuntimeState.STOPPING,
    }
)


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
    # Stands in for "no Bot Session yet," so the chart has something to show
    # on the Setup screen instead of staying blank until one is created.
    default_session: ConfiguredPaperSession | None = None


# Frozen and empty, so one instance can stand for "none of this is wired".
NO_BOT = BotActions()
NO_CHART = ChartSources()


type SaveLineNotifications = Callable[[bool, str, str], None]
"""Keep these LINE settings, wherever this installation keeps them."""

type ReadLineNotifications = Callable[[], LineNotificationsReading]
"""Read back the LINE settings — never the token, only that there is one."""


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        create_session: CreateSession,
        load_active: LoadActiveSession,
        list_basket_history: ListBasketHistory | None = None,
        list_trade_fills: ListTradeFills | None = None,
        read_total_realized_pnl: ReadTotalRealizedPnl | None = None,
        validate_session: Callable[[PaperSessionSetupValues], None] | None = None,
        bot: BotActions = NO_BOT,
        chart: ChartSources = NO_CHART,
        thread_pool: QThreadPool | None = None,
        take_profit_confirmation: TakeProfitConfirmation | None = None,
        blocked_notice: BlockedNotice | None = None,
        connection_notice: ConnectionNotice | None = None,
        connection_notice_delay_ms: int = CONNECTION_NOTICE_DELAY_MILLISECONDS,
        clear_database: Callable[[], None] | None = None,
        save_line_notifications: SaveLineNotifications | None = None,
        read_line_notifications: ReadLineNotifications | None = None,
    ) -> None:
        super().__init__()
        self._take_profit_confirmation = (
            take_profit_confirmation or self._open_take_profit_confirmation
        )
        self._blocked_notice = blocked_notice or self._open_blocked_notice
        self._connection_lost = False
        self._last_bot_state: BotRuntimeState | None = None
        self._take_profit_confirmation_pending = False
        self._take_profit_confirmation_dialog: QMessageBox | None = None
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._load_chart = chart.load
        self._close_position = bot.close_position
        self._close_entry = bot.close_entry
        self._cancel_cooldown = bot.cancel_cooldown
        self._waiting_on_a_reading: list[Callable[[], None]] = []
        self._has_open_position = bot.has_open_position
        self._clock = chart.clock
        self._retire_session = bot.retire_session
        self._stop_for_replacement = bot.stop_for_replacement
        self._validate_session = validate_session or validate_paper_session
        self._shutdown_runtime = bot.shutdown_runtime
        self._clear_database = clear_database
        self._save_line_notifications = save_line_notifications
        self._read_line_notifications = read_line_notifications
        self._chart_fact_relay = chart.facts
        self._start_chart_feed = chart.start_feed
        self._select_chart_view = chart.select_view
        self._stop_chart_feed = chart.stop_feed
        self._default_chart_session = chart.default_session
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
        self._connection_notice = (
            connection_notice or self.workspace.connection_notice_card
        )
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
        self._history_started = False
        # Whether a Basket is open right now, read across whichever side is
        # holding it — the running Runtime or the database. A close is the
        # moment this goes from true to false, wherever it is seen.
        self._position_open = False
        # Which Entries the open Basket is holding. Every sale changes it,
        # and a sale is the only thing that changes what has been realised,
        # so this is what says the durable half has to be read again. `None`
        # is no Basket at all, which is also a composition.
        self._position_composition: tuple[int, ...] | None = None

        # Built in the order the window reads: the Session first, because
        # everything below is about the one it is on, and the history last,
        # because it outlives every Session there has been.
        self._wire_session_workflow(create_session, load_active)
        self._build_guarded_actions()
        self._wire_open_position(bot)
        self._wire_bot_lifecycle(bot)
        self._wire_chart(chart, bot, connection_notice_delay_ms)
        self._wire_trade_history(list_basket_history, list_trade_fills)
        self._wire_total_pnl(read_total_realized_pnl)
        self._workflow.start()
        self._total_pnl.start()

    def _wire_session_workflow(
        self, create_session: CreateSession, load_active: LoadActiveSession
    ) -> None:
        """The workflow that reads, creates and validates the Paper Session."""
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

    def _wire_open_position(self, bot: BotActions) -> None:
        """The Position tab, and the three things it lets the trader do."""
        self._open_position = OpenPositionWorkflow(
            read_open_basket=bot.read_open_basket,
            thread_pool=self._thread_pool,
            clock=self._clock,
            parent=self,
        )
        self._open_position.snapshot_changed.connect(
            self.workspace.position_basket.show_snapshot
        )
        self._open_position.snapshot_changed.connect(self._open_position_basket_changed)
        self.workspace.position_basket.close_entry_requested.connect(
            self._close_entry_requested
        )
        self.workspace.position_basket.cancel_cooldown_requested.connect(
            self._cancel_cooldown_requested
        )
        self.workspace.position_basket.close_all_requested.connect(
            self._close_all_requested
        )
        self.workspace.settings_requested.connect(self._settings_requested)
        self.workspace.settings_panel.clear_database_requested.connect(
            self._clear_database_requested
        )
        self.workspace.settings_panel.line_notifications_save_requested.connect(
            self._line_notifications_save_requested
        )
        self._open_position.reading_landed.connect(self._position_reading_landed)

    def _wire_bot_lifecycle(self, bot: BotActions) -> None:
        """Starting and stopping the Bot, and hearing what state it is in."""
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

    def _wire_chart(
        self, chart: ChartSources, bot: BotActions, connection_notice_delay_ms: int
    ) -> None:
        """The chart, the candles that reach it, and the notices it raises."""
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
            sale_is_unknown=self._open_position.what_a_start_sells_is_unknown(),
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
        self._once_the_position_is_read(
            self._current_session(), self._ask_before_starting
        )

    def _current_session(self) -> ConfiguredPaperSession | None:
        """The Session this window is on, or None before there is one.

        The lifecycle workflow raises rather than answers when no Session has
        been created yet, and every caller here treats that the same way it
        treats any other reason there is nothing to act on.
        """
        try:
            return self._lifecycle_workflow.snapshot.session
        except RuntimeError:
            return None

    def _build_guarded_actions(self) -> None:
        """The things the trader asks for that run off the UI thread.

        Each one switches its own control off while it runs, says what went
        wrong where that control speaks, and comes back either way — which is
        the whole of what they have in common, and all that is stated here.
        """
        position_basket = self.workspace.position_basket
        settings_panel = self.workspace.settings_panel
        self._sell_entry = GuardedAction(
            available=position_basket.set_closing_enabled,
            report_failure=lambda: position_basket.show_close_error(
                "Could not sell that Entry. Check your connection, then try again."
            ),
            failure_log="Could not sell that Entry",
            start=self._thread_pool.start,
        )
        self._waive_cooldown = GuardedAction(
            available=position_basket.set_cancelling_enabled,
            report_failure=lambda: position_basket.show_cancel_error(
                "Could not waive the Cooldown. Check your connection, then try again."
            ),
            failure_log="Could not waive the Cooldown",
            start=self._thread_pool.start,
        )
        self._sell_position = GuardedAction(
            available=position_basket.set_selling_enabled,
            report_failure=lambda: position_basket.show_close_error(
                "Could not close the Position. Check your connection, then try again."
            ),
            failure_log="Could not close the Position",
            start=self._thread_pool.start,
        )
        # The Settings panel has no line of its own to answer on, and says
        # "busy" where the Position tab says which control is off.
        self._wipe_database = GuardedAction(
            available=lambda available: settings_panel.set_busy(not available),
            report_failure=lambda: QMessageBox.warning(
                settings_panel,
                "Settings",
                "Could not clear the database. Try again.",
            ),
            failure_log="Could not clear the database",
            start=self._thread_pool.start,
        )
        self._keep_line_settings = GuardedAction(
            available=lambda available: settings_panel.set_busy(not available),
            report_failure=lambda: QMessageBox.warning(
                settings_panel,
                "Settings",
                "Could not save the LINE settings. "
                "This machine may not be letting TiewTrade store them.",
            ),
            failure_log="Could not save the LINE notification settings",
            start=self._thread_pool.start,
        )
        # Reading is its own action rather than a second use of the one that
        # saves: what is saved is read back straight afterwards, and an action
        # already running is an action that refuses to start again.
        self._show_line_settings = GuardedAction(
            available=lambda available: settings_panel.set_busy(not available),
            report_failure=settings_panel.say_line_notifications_unreadable,
            failure_log="Could not read the LINE notification settings",
            start=self._thread_pool.start,
        )

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
        message = "Could not stop, close, or retire the existing Paper Session."
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
        elif str(_error) == "Active Paper Session is no longer there":
            message = "The existing Paper Session is no longer in the database."
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
        self._start_chart_for(session)

    def _start_chart_for(self, session: ConfiguredPaperSession) -> None:
        self.workspace.live_price_card.show_market_type(session.config.market_type)
        self.workspace.live_price_card.show_pair(
            session.market_data.symbol,
            session.market_data.timeframe,
        )
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
        # Only the running Runtime publishes the Basket here — a stopped
        # one's copy is read through `_open_position` instead, so reading
        # this snapshot while stopped would mistake the ownership hand-off
        # for a close.
        if (
            isinstance(value, TradingWorkspaceSnapshot)
            and self._last_bot_state is BotRuntimeState.RUNNING
        ):
            self._note_position(value.position_basket.basket)

    @Slot(object)
    def _open_position_basket_changed(self, value: object) -> None:
        if not isinstance(value, PositionBasketTabSnapshot):
            return
        if self._last_bot_state is BotRuntimeState.RUNNING:
            # The Runtime owns the Basket, and this side can still land a
            # read that went out before the hand-off — an empty tab from
            # before the Bot started, over a Position that is open now.
            return
        self._note_position(value.basket)

    def _note_position(self, basket: BasketSnapshot | None) -> None:
        """Take the Position as it now stands, wherever it was read from.

        Two things follow from it. A closed Basket is a new history row
        nothing else will surface until this Session's tab is reopened, so
        the close itself is what refreshes the table. And the Basket is half
        of the total: what it is worth now comes straight off the snapshot,
        while what it has realised is in the database — read again whenever
        the Entries it holds change, which is every sale and nothing else.
        """
        was_open = self._position_open
        self._position_open = basket is not None
        if was_open and basket is None and self._history_started:
            self._history_workflow.start()
        self._total_pnl.set_open_basket(basket)
        composition = (
            None
            if basket is None
            else tuple(entry.entry_number for entry in basket.entries)
        )
        if composition != self._position_composition:
            self._position_composition = composition
            self._total_pnl.refresh()

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
            "The Session is open on the setup form. Start the Bot again once "
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

    def _show_live_price(self, candle: object) -> None:
        """Put the price on the sidebar card, whatever the chart makes of it.

        The chart takes a forming candle only while it is following the
        present, so panning back a day used to take the current price off
        the screen entirely. The card is fed here, before the workflow has
        had its say, so the price stays readable wherever the chart is.
        """
        if not isinstance(candle, ChartCandle):
            return
        self.workspace.live_price_card.show_price(candle)

    @Slot(object, int)
    def _chart_completed_candle(self, candle: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._show_live_price(candle)
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
        self.workspace.live_price_card.set_stale(state is not ChartFeedState.LIVE)
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
        session = self._current_session()
        if session is None:
            return
        self._start_chart_feed(session)

    @Slot(object, int)
    def _chart_in_progress_candle(self, candle: object, generation: int) -> None:
        relay = self._chart_fact_relay
        if relay is None or not relay.is_current(generation):
            return
        self._show_live_price(candle)
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
        if close_entry is None or self._sell_entry.is_running:
            return
        session = self._current_session()
        if session is None:
            return
        if not ask_about_selling_one_entry(self, entry_number):
            return
        self._sell_entry.run(
            lambda: close_entry(session, entry_number),
            succeeded=self._position_action_landed,
        )

    @Slot(object)
    def _position_action_landed(self, _result: object) -> None:
        """Read the Basket back, which is how a stopped Bot's tab hears of it.

        A running Runtime publishes what it did itself — the sale, the sold
        Entry, the lifted gate. A stopped one leaves the database as the only
        witness, and the tab is showing what it last read from it, which is
        the state before the action landed.
        """
        session = self._current_session()
        if session is None:
            return
        if self._lifecycle_workflow.snapshot.state is not BotRuntimeState.RUNNING:
            self._open_position.read_for(session)

    @Slot()
    def _cancel_cooldown_requested(self) -> None:
        """Lift a Cooldown, once the trader has said so in as many words.

        Which of the two is holding the Basket is not this window's to know:
        the action is asked, and it goes to the Runtime that owns it or to the
        database a stopped Bot left it in.
        """
        cancel_cooldown = self._cancel_cooldown
        if cancel_cooldown is None or self._waive_cooldown.is_running:
            return
        basket = self.workspace.position_basket.basket
        if basket is None or basket.entry_gate is not BasketEntryGate.COOLDOWN:
            return
        session = self._current_session()
        if session is None:
            return
        if not ask_about_cancelling_cooldown(self):
            return
        self._waive_cooldown.run(
            lambda: cancel_cooldown(session),
            succeeded=self._position_action_landed,
        )

    @Slot()
    def _close_all_requested(self) -> None:
        """Sell the whole Position, once the trader has said so in as many words.

        Which of the two is holding the Basket is not this window's to know:
        the action is asked, and it goes to the Runtime that owns it or to the
        database a stopped Bot left it in.
        """
        close_position = self._close_position
        if close_position is None or self._sell_position.is_running:
            return
        session = self._current_session()
        if session is None:
            return
        if not ask_about_closing_all_at_market(self):
            return
        self._sell_position.run(
            lambda: close_position(session),
            succeeded=self._position_action_landed,
        )

    @Slot()
    def _settings_requested(self) -> None:
        if self.workspace.is_showing_settings:
            self.workspace.hide_settings()
            return
        self.workspace.show_settings()
        self._read_line_settings()

    def _read_line_settings(self) -> None:
        """Fill the LINE form from what is stored, off the UI thread.

        Wherever the settings are kept, reading them can put a prompt in front
        of the trader and wait on an answer, which is not something a window
        may do while it is being drawn. The form opens as it is and fills in
        when the reading lands.
        """
        read = self._read_line_notifications
        if read is None or self._show_line_settings.is_running:
            return
        self._show_line_settings.run(read, succeeded=self._line_settings_read)

    @Slot(object)
    def _line_settings_read(self, reading: object) -> None:
        if not isinstance(reading, tuple):
            return
        self.workspace.settings_panel.show_line_notifications(reading)

    @Slot(bool, str, str)
    def _line_notifications_save_requested(
        self,
        enabled: bool,
        target_id: str,
        access_token: str,
    ) -> None:
        save = self._save_line_notifications
        if save is None or self._keep_line_settings.is_running:
            return
        self._keep_line_settings.run(
            lambda: save(enabled, target_id, access_token),
            succeeded=self._line_settings_kept,
        )

    @Slot(object)
    def _line_settings_kept(self, _result: object) -> None:
        """Read the form back from what was actually stored.

        What the trader typed and what is now stored are two things, and the
        second is the one the Bot will use. Reading it back is also what
        empties the token field once its value is somewhere safer.
        """
        self._read_line_settings()

    @Slot()
    def _clear_database_requested(self) -> None:
        clear_database = self._clear_database
        if clear_database is None or self._wipe_database.is_running:
            return
        if self._last_bot_state in _CLEAR_DATABASE_BLOCKED_STATES:
            QMessageBox.warning(
                self.workspace.settings_panel,
                "Settings",
                "Stop the bot before clearing the database.",
            )
            return
        if not ask_about_clearing_database(self.workspace.settings_panel):
            return
        self._wipe_database.run(clear_database, succeeded=self._database_cleared)

    @Slot(object)
    def _database_cleared(self, _result: object) -> None:
        """Read the Position tab back, the same way a Close All does.

        Whatever Basket the tab was showing had its row wiped along with
        everything else. Left alone, a stopped Session's Close All would
        still read as available and, pressed, find nothing left to sell —
        so the tab is asked again, the way `_all_closed` already does after
        an actual sale.

        The chart is read back for the same reason: its Buy/Sell badges are
        drawings of fills whose rows are gone, and nothing else would ask
        for them again until the next trade.
        """
        self._history_workflow.start()
        self._total_pnl.refresh()
        self._chart_workflow.refresh_fills()
        session = self._current_session()
        if session is not None:
            self._open_position.read_for(session)
        # The Session the setup form was holding to replace was wiped along
        # with the rest, so there is nothing left to stop, close, or retire.
        # Held on to, it sent the next Start to retire a row that was gone,
        # which came back as "still running somewhere" — about a Session
        # nothing was running. A cleared installation has no Session, and
        # the next Start is the one that makes one.
        self._replacement_session = None
        self._replacement_values = None
        self.workspace.hide_settings()

    @Slot(object)
    def _chart_view_requested(self, value: object) -> None:
        if not isinstance(value, ChartViewSelection):
            return
        session = self._current_session()
        if session is None:
            session = self._default_chart_session
        if session is None:
            return
        self._chart_workflow.select_view(value)
        self.workspace.live_price_card.clear()
        self.workspace.live_price_card.show_pair(value.symbol, value.timeframe)
        if self._select_chart_view is not None:
            self._select_chart_view(session, value)

    @Slot(object, int)
    def _runtime_completed_candle(self, candle: object, generation: int) -> None:
        relay = self._runtime_snapshots
        if relay is None or not relay.is_current(generation):
            return
        self._show_live_price(candle)
        self._chart_workflow.completed_candle(candle)

    @Slot(object, int)
    def _runtime_in_progress_candle(self, candle: object, generation: int) -> None:
        relay = self._runtime_snapshots
        if relay is None or not relay.is_current(generation):
            return
        self._show_live_price(candle)
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
        if self._default_chart_session is not None:
            self._start_chart_for(self._default_chart_session)

    @Slot()
    def _trade_history_activated(self) -> None:
        """Note that history has been looked at; the tab itself asks for it.

        Opening a history tab already announces which half it wants, and
        that announcement is what reads it — every visit, not only the
        first. All that is left here is remembering that the trader has
        been there, so a Basket closing later knows to refresh the table.
        """
        self._history_started = True

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
        self.workspace.live_price_card.clear()
        self._workflow.close()
        self._lifecycle_workflow.close()
        self._history_workflow.close()
        self._total_pnl.close()
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

    def _wire_total_pnl(
        self, read_total_realized_pnl: ReadTotalRealizedPnl | None
    ) -> None:
        """The figure beside the price: everything realised, plus what is held."""
        self._total_pnl = TotalPnlWorkflow(
            read_total_realized_pnl=read_total_realized_pnl,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._total_pnl.total_changed.connect(
            self.workspace.live_price_card.show_total_pnl
        )

    def _wire_trade_history(
        self,
        list_basket_history: ListBasketHistory | None,
        list_trade_fills: ListTradeFills | None,
    ) -> None:
        """The durable history tables, and what asks them to read again."""
        self._history_workflow = TradeHistoryWorkflow(
            list_basket_history=list_basket_history,
            list_trade_fills=list_trade_fills,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self.workspace.trade_history_activated.connect(self._trade_history_activated)
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
