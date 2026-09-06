import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from tiewtrade.application.bot_control import BotControlAction, BotControlSnapshot
from tiewtrade.application.chart_data import (
    ChartCandle,
    ChartFeedState,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_replacement import (
    replacement_failure_message,
)
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
    PaperSessionValidationError,
    validate_paper_session,
)
from tiewtrade.application.session_notices import ListSessionNotices
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    BotRuntimeState,
    PositionBasketTabSnapshot,
    TradingWorkspaceSnapshot,
)
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.bot_lifecycle_workflow import (
    BotLifecycleWorkflow,
    LifecycleAction,
    RuntimeSnapshotRelay,
)
from tiewtrade.ui.chart_fact_relay import ChartFactRelay
from tiewtrade.ui.chart_workflow import (
    ChartWorkflow,
    LoadChart,
    LoadChartFills,
    RefreshChart,
)
from tiewtrade.ui.connection_notice import ConnectionNotice
from tiewtrade.ui.guarded_action import GuardedAction
from tiewtrade.ui.notification_workflow import NotificationWorkflow
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
from tiewtrade.ui.settings_workflow import (
    ReadBotTitle,
    ReadLineNotifications,
    ReadTelegramNotifications,
    SaveBotTitle,
    SaveLineNotifications,
    SaveTelegramNotifications,
    SettingsWorkflow,
)
from tiewtrade.ui.start_decision import StartCourse, course_before_start
from tiewtrade.ui.start_dialogs import (
    PositionAtTarget,
    PositionOnStart,
    ask_about_a_position_at_its_target,
    ask_about_the_open_position,
)
from tiewtrade.ui.start_workflow import StartWorkflow, TakeProfitConfirmation
from tiewtrade.ui.system_notice import (
    CONNECTION_NOTICE_DELAY_MILLISECONDS,
    SystemNoticeWorkflow,
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

# Told to the trader, not asked of them: the Bot has already stopped by the
# time this is called. A plain callable, so a test can read what was said.
BlockedNotice = Callable[[str], None]


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
    # Clears the Session that is there away — stopped, closed and retired —
    # so the setup form's values can make a new one. The order that happens
    # in is a business rule, and it is `application` that states it.
    replace_session: Callable[[ConfiguredPaperSession], None] | None = None
    close_position: Callable[[ConfiguredPaperSession], None] | None = None
    # One Entry of the open Basket, sold at the price the market is at. True
    # where it sold, False where there was no such Entry left to sell.
    close_entry: Callable[[ConfiguredPaperSession, int], bool] | None = None
    # Lifts the Cooldown blocking the open Basket's next Entry. True where
    # one was blocking it and got lifted, False otherwise.
    cancel_cooldown: Callable[[ConfiguredPaperSession], bool] | None = None
    has_open_position: Callable[[ConfiguredPaperSession], bool] | None = None
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
        save_telegram_notifications: SaveTelegramNotifications | None = None,
        read_telegram_notifications: ReadTelegramNotifications | None = None,
        save_bot_title: SaveBotTitle | None = None,
        read_bot_title: ReadBotTitle | None = None,
        list_session_notices: ListSessionNotices | None = None,
    ) -> None:
        super().__init__()
        self._blocked_notice = blocked_notice or self._open_blocked_notice
        self._last_bot_state: BotRuntimeState | None = None
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._load_chart = chart.load
        self._close_position = bot.close_position
        self._close_entry = bot.close_entry
        self._cancel_cooldown = bot.cancel_cooldown
        self._has_open_position = bot.has_open_position
        self._clock = chart.clock
        self._replace_paper_session = bot.replace_session
        self._validate_session = validate_session or validate_paper_session
        self._shutdown_runtime = bot.shutdown_runtime
        self._chart_fact_relay = chart.facts
        self._start_chart_feed = chart.start_feed
        self._select_chart_view = chart.select_view
        self._stop_chart_feed = chart.stop_feed
        self._default_chart_session = chart.default_session
        self._close_sequence_started = False
        self._close_sequence_completed = False
        self._shutdown_task: BackgroundTask | None = None
        self._replacement_task: BackgroundTask | None = None
        self._runtime_shutdown_pool = QThreadPool(self)
        self._runtime_shutdown_pool.setMaxThreadCount(1)
        self.workspace = TradingWorkspace()
        self._settings = SettingsWorkflow(
            panel=self.workspace.settings_panel,
            clear_database=clear_database,
            save_line_notifications=save_line_notifications,
            read_line_notifications=read_line_notifications,
            save_telegram_notifications=save_telegram_notifications,
            read_telegram_notifications=read_telegram_notifications,
            save_bot_title=save_bot_title,
            read_bot_title=read_bot_title,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._settings.database_cleared.connect(self._database_cleared)
        # The toolbar wears whatever this installation is called, from the
        # first frame and again whenever it is changed on the Settings page.
        self._settings.bot_title_changed.connect(self.workspace.show_bot_title)
        self._settings.refresh_bot_title()
        self._system_notice = SystemNoticeWorkflow(
            connection_notice=(
                connection_notice or self.workspace.connection_notice_card
            ),
            connection_notice_delay_ms=connection_notice_delay_ms,
            parent=self,
        )
        self._system_notice.notice_changed.connect(
            self.workspace.chart.show_system_notice
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
        self._wire_bot_lifecycle(bot, take_profit_confirmation)
        self._wire_chart(chart, bot)
        self._wire_trade_history(list_basket_history, list_trade_fills)
        self._wire_notifications(list_session_notices)
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

    def _wire_bot_lifecycle(
        self, bot: BotActions, take_profit_confirmation: TakeProfitConfirmation | None
    ) -> None:
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
        self._start = StartWorkflow(
            position=self._open_position,
            current_session=self._current_session,
            start_bot=self._lifecycle_workflow.start_bot,
            dialog_parent=self,
            confirmation=take_profit_confirmation,
            parent=self,
        )
        self.workspace.start_bot_requested.connect(self._start.requested)
        self.workspace.stop_bot_requested.connect(self._stop_requested)

    def _wire_chart(self, chart: ChartSources, bot: BotActions) -> None:
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
        self._open_position.read_then(
            self._replacement_session, self._take_the_start_course
        )

    def _take_the_start_course(self) -> None:
        if self._replacement_values is None or self._replacement_session is None:
            return
        course = course_before_start(
            position_open=self._position_is_open(),
            sells_on_start=self._start.sells_on_start_message() is not None,
            sale_is_unknown=self._open_position.what_a_start_sells_is_unknown(),
        )
        if course is StartCourse.ASK_ABOUT_THE_SALE:
            self._act_on_a_position_at_its_target()
            return
        if course is StartCourse.ASK_ABOUT_THE_POSITION:
            self._act_on_the_open_position()
            return
        self._replace_session()

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
        reached = self._start.position_at_target_message()
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
            self._start.position_held_message() or UNREADABLE_POSITION_MESSAGE,
            # Said only where part of the Position has reached a target, which
            # is what makes running the Session on a sale of its own.
            self._start.position_at_target_message(),
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
        self._start.start_now()

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

    def _replace_session(self) -> None:
        session = self._replacement_session
        values = self._replacement_values
        replace = self._replace_paper_session
        if session is None or values is None:
            return
        if replace is None:
            self.setup.show_field_error(
                "capital_per_entry",
                "Paper Session replacement is unavailable. Restart the application.",
            )
            return
        if self._replacement_task is not None:
            return
        self.workspace.set_bot_busy(True)
        self._replacement_succeeded = False
        task = BackgroundTask(lambda: replace(session))
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
    def _replacement_failed(self, error: object) -> None:
        message = (
            replacement_failure_message(error)
            if isinstance(error, BaseException)
            else "Could not stop, close, or retire the existing Paper Session."
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
        self._settings.set_bot_state(value.state)
        self._system_notice.bot_snapshot(value, previous_state)
        self._notification_workflow.show_session(value.session.config.session_id)
        self.workspace.show_bot_control_snapshot(value)
        self._chart_workflow.runtime_snapshot(value)
        if value.state is BotRuntimeState.RUNNING:
            # The running Runtime publishes its Basket here and nowhere else,
            # and while it holds one this side reads none. Without taking it,
            # the total left out everything a running Bot was holding.
            self._note_position(value.workspace.position_basket.basket)
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
            self._start.requested()
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
        self._notification_workflow.show_session(None)
        self.workspace.show_setup()

    def _still_on_screen(
        self,
        relay: ChartFactRelay | RuntimeSnapshotRelay | None,
        generation: int,
    ) -> bool:
        """Whether a candle relayed at this generation is still the one on screen.

        A relay hands its facts over from a worker thread, so one can land
        after the Session it was read for has gone. The generation it carries
        is what says which, and an unwired relay never had one to be current.
        """
        return relay is not None and relay.is_current(generation)

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
        # The same price the card is showing marks the open Basket, so the
        # total beside it moves with the price rather than once a candle. A
        # Basket a running Runtime owns is re-marked nowhere else.
        self._total_pnl.set_current_price(candle.symbol, candle.close)

    @Slot(object, int)
    def _chart_completed_candle(self, candle: object, generation: int) -> None:
        if not self._still_on_screen(self._chart_fact_relay, generation):
            return
        self._show_live_price(candle)
        self._mark_chart_candle(candle)
        self._chart_workflow.completed_candle(candle)

    @Slot(object, int)
    def _chart_feed_state_received(self, state: object, generation: int) -> None:
        if not self._still_on_screen(self._chart_fact_relay, generation):
            return
        self._chart_workflow.feed_state(state)

    @Slot(object)
    def _show_chart_feed_state(self, state: object) -> None:
        if not isinstance(state, ChartFeedState):
            return
        self.workspace.chart.show_feed_state(state)
        self.workspace.live_price_card.set_stale(state is not ChartFeedState.LIVE)
        self._system_notice.feed_state(state)

    @Slot()
    def _chart_retry_requested(self) -> None:
        """Read the history again, and put a feed back under it if it is gone.

        A chart whose feed has stopped is two faults at once, and reloading
        the history alone answers only the half the reader can see: the
        candles come back and then stop arriving again immediately.
        """
        self._chart_workflow.retry()
        if (
            self._system_notice.chart_feed_state is not ChartFeedState.STOPPED
            or self._start_chart_feed is None
        ):
            return
        session = self._current_session()
        if session is None:
            return
        self._start_chart_feed(session)

    @Slot(object, int)
    def _chart_in_progress_candle(self, candle: object, generation: int) -> None:
        if not self._still_on_screen(self._chart_fact_relay, generation):
            return
        self._show_live_price(candle)
        self._mark_chart_candle(candle)
        self._chart_workflow.in_progress_candle(candle)

    def _ask_then_act(
        self,
        action: GuardedAction,
        ask: Callable[[], bool],
        work: Callable[[ConfiguredPaperSession], object],
    ) -> None:
        """Put the question, and act on the Session where the answer is yes.

        The three things the Position tab does are asked and answered alike:
        nothing starts while the same action is still in flight, nothing acts
        without a Session to act on, and nothing acts until the trader has
        said so in as many words. Which of the two is holding the Basket is
        not this window's to know — the action goes to the Runtime that owns
        it or to the database a stopped Bot left it in.
        """
        if action.is_running:
            return
        session = self._current_session()
        if session is None:
            return
        if not ask():
            return
        action.run(lambda: work(session), succeeded=self._position_action_landed)

    @Slot(int)
    def _close_entry_requested(self, entry_number: int) -> None:
        """Sell one Entry, at the price the market is at."""
        close_entry = self._close_entry
        if close_entry is None:
            return
        self._ask_then_act(
            self._sell_entry,
            lambda: ask_about_selling_one_entry(self, entry_number),
            lambda session: close_entry(session, entry_number),
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
        """Lift the Cooldown, where one is what the open Basket is waiting on."""
        cancel_cooldown = self._cancel_cooldown
        if cancel_cooldown is None:
            return
        basket = self.workspace.position_basket.basket
        if basket is None or basket.entry_gate is not BasketEntryGate.COOLDOWN:
            return
        self._ask_then_act(
            self._waive_cooldown,
            lambda: ask_about_cancelling_cooldown(
                self, cooldown_ends_at=basket.cooldown_ends_at_utc
            ),
            cancel_cooldown,
        )

    @Slot()
    def _close_all_requested(self) -> None:
        """Sell the whole Position, at the price the market is at."""
        close_position = self._close_position
        if close_position is None:
            return
        self._ask_then_act(
            self._sell_position,
            lambda: ask_about_closing_all_at_market(self),
            close_position,
        )

    @Slot()
    def _settings_requested(self) -> None:
        if self.workspace.is_showing_settings:
            self.workspace.hide_settings()
            return
        self.workspace.show_settings()
        self._settings.opened()

    @Slot()
    def _database_cleared(self) -> None:
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
        # The chart is being pointed at another Pair, and the price that was
        # on the card is not one the Basket can be marked on any more.
        self._total_pnl.clear_price()
        self.workspace.live_price_card.show_pair(value.symbol, value.timeframe)
        if self._select_chart_view is not None:
            self._select_chart_view(session, value)

    @Slot(object, int)
    def _runtime_completed_candle(self, candle: object, generation: int) -> None:
        if not self._still_on_screen(self._runtime_snapshots, generation):
            return
        self._show_live_price(candle)
        self._chart_workflow.completed_candle(candle)

    @Slot(object, int)
    def _runtime_in_progress_candle(self, candle: object, generation: int) -> None:
        if not self._still_on_screen(self._runtime_snapshots, generation):
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
        self._notification_workflow.show_session(None)
        self.workspace.show_unavailable(message)

    @Slot()
    def _show_setup(self) -> None:
        self._notification_workflow.show_session(None)
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

    def present(self) -> None:
        """Bring this window to the trader, wherever they left it.

        Asked for by a second launch of the app: one installation runs one Bot
        Session, so opening it again is a request for the window that is
        already open rather than for another one.

        All four steps are needed, and none of them stands for the others. A
        window that was never shown needs `show()`. A minimized one is still
        "visible" to Qt, so the minimized bit has to come off explicitly —
        and only that bit, or a maximized window would come back the wrong
        size. A window behind others needs raising, and a raised window that
        is not active still leaves the trader's keystrokes going elsewhere.

        A window part-way through closing is left alone. Its Runtime is
        already stopped and it is waiting on its workers, so putting it in
        front of a trader would show them a window that is about to vanish.
        """
        if self._close_sequence_started:
            return
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        # A window manager is entitled to refuse a raise it did not see a user
        # ask for — Wayland compositors usually do. Then this is all the app
        # can do: mark the window as wanting attention, and let the taskbar or
        # the dock say so.
        QApplication.alert(self)

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
        self._system_notice.close()
        if self._chart_fact_relay is not None:
            self._chart_fact_relay.invalidate()
        self.workspace.live_price_card.clear()
        self._workflow.close()
        self._lifecycle_workflow.close()
        self._history_workflow.close()
        self._notification_workflow.close()
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
        self._history_workflow.attach(self.trade_history)
        self.workspace.trade_history_activated.connect(self._trade_history_activated)

    def _wire_notifications(
        self, list_session_notices: ListSessionNotices | None
    ) -> None:
        """The Notification tab, read only while a trader has it open.

        Scoped to whichever Session is on screen: a notice belongs to the
        Session that raised it the same way a Fill does, and a trader reading
        this tab is reading about the Bot in front of them, not every Bot
        this installation has ever run.
        """
        self._notification_workflow = NotificationWorkflow(
            list_session_notices=list_session_notices,
            thread_pool=self._thread_pool,
            parent=self,
        )
        self._notification_workflow.attach(self.workspace.notifications)
        self.workspace.notifications_activated.connect(
            self._notification_workflow.activate
        )
        self.workspace.notifications_deactivated.connect(
            self._notification_workflow.deactivate
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
