import asyncio
import logging
import os
import sys
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Mapping,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import sleep
from uuid import UUID

from tiewtrade.application.chart_data import (
    ChartRange,
)
from tiewtrade.application.chart_history import ChartHistory
from tiewtrade.application.chart_realtime_feed import ChartOnlyFeedController
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.database_compatibility import DatabaseCompatibilityError
from tiewtrade.application.latest_market_price import LatestMarketPrice
from tiewtrade.application.manual_close_price import ManualClosePrice
from tiewtrade.application.open_basket import ReopenedBasket, reopen_basket
from tiewtrade.application.paper_basket_at_rest import (
    close_one_entry_at_rest,
    waive_cooldown_at_rest,
)
from tiewtrade.application.paper_manual_close import ClosePaperPosition
from tiewtrade.application.paper_runtime import (
    read_open_basket_state,
)
from tiewtrade.application.paper_session_replacement import (
    ReplacePaperSession,
)
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
    validate_paper_session,
)
from tiewtrade.application.paper_symbols import PAPER_SYMBOLS, paper_symbol_rules
from tiewtrade.decimal_context import configure_decimal_context
from tiewtrade.desktop_logging import PACKAGE_LOGGER, configure_desktop_logging
from tiewtrade.desktop_paper_runtime import PaperRuntimeActions
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.binance.public_market_data import BinancePublicMarketData
from tiewtrade.integrations.process_lock.installation_lock import InstallationLock
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import (
    SQLiteDatabase,
    UnsupportedDatabaseSchemaError,
)
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.line_notification_composition import (
    line_notification_reading,
    line_session_notifier,
    save_line_notification_settings,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import TradeFill
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.chart_fact_relay import ChartFactRelay
from tiewtrade.ui.desktop import (
    report_installation_in_use,
    the_qt_application,
)
from tiewtrade.ui.desktop import run_desktop as run_desktop_ui
from tiewtrade.ui.instance_activation import (
    InstanceActivation,
    activation_name_for,
)
from tiewtrade.ui.main_window import BotActions, ChartSources

# Named outright rather than taken from `__name__`, which is the one thing
# this module cannot use. `python -m tiewtrade.desktop_main` — the command
# this repository documents for running the app — makes `__name__` equal to
# `"__main__"`, which sits outside the package logger and so outside the
# handler `configure_desktop_logging` attaches. Every line below would be
# written to a logger nothing is listening to, and a developer looking for
# why the app refused to start would find an empty log.
_logger = logging.getLogger(f"{PACKAGE_LOGGER}.desktop_main")

APPLICATION_NAME = "TiewTrade"
DATABASE_FILE_NAME = "tiewtrade.sqlite3"

_MANUAL_CLOSE_PRICE_MAX_AGE = timedelta(seconds=30)
# Not 0: a shell, a launcher, or a packaging script has to be able to tell
# "the app ran and closed" from "the app never opened".
_INSTALLATION_IN_USE_EXIT_CODE = 1
_INSTALLATION_IN_USE_MESSAGE = (
    "TiewTrade is already open in another window. "
    "One installation runs one Bot Session at a time, so close the other "
    "window before opening this one."
)
# Both ends are on this machine, so an answer either comes back at once or is
# not coming, and the timeout is a bound on a request that has gone wrong.
_ACTIVATION_TIMEOUT_MS = 1_000
# The attempts are for a different thing entirely: the process that holds the
# installation but has not begun listening yet. Nothing is there to refuse the
# connection, so the ask fails instantly and retrying without waiting would
# spend all three attempts inside the same millisecond. The wait is what makes
# them attempts — a Qt window takes a moment to build, and this covers it.
_ACTIVATION_ATTEMPTS = 3
_ACTIVATION_RETRY_WAIT_SECONDS = 0.5


def _symbol_rules_for(session: ConfiguredPaperSession) -> SymbolRules:
    return paper_symbol_rules(
        session.market_data.symbol,
        session.config.market_type,
    )


def _preset_for(session: ConfiguredPaperSession) -> StrategyPreset:
    """The Strategy Preset this Session was pinned to, built afresh."""
    return strategy_definition_for(session.config.preset_version).build_preset()


@asynccontextmanager
async def _public_market_data(
    session: ConfiguredPaperSession,
) -> AsyncIterator[BinancePublicMarketData]:
    """A public market-data source for a Session, closed however the work ends.

    The source holds a connection, so every use is a borrow rather than a
    build: leaving one open after a failed read is a socket the desktop keeps
    for the rest of its run.
    """
    source = BinancePublicMarketData(
        BinancePublicEndpoints.for_market_type(session.config.market_type)
    )
    try:
        yield source
    finally:
        await source.close()


def _migrate_first[**Params, Result](
    prepare_database: Callable[[], None],
    work: Callable[Params, Result],
) -> Callable[Params, Result]:
    """The same work, with the database brought up to date before it runs.

    Migration is deferred until something actually needs the file, so that a
    desktop that never opens a Session never touches it. That makes "has the
    database been prepared?" a question every entry point has to answer, and
    the answer is the same one each time — so it is asked here rather than as
    a first line each of them could forget.
    """

    def with_prepared_database(*args: Params.args, **kwargs: Params.kwargs) -> Result:
        prepare_database()
        return work(*args, **kwargs)

    return with_prepared_database


def _migrate_first_async[**Params, Result](
    prepare_database: Callable[[], None],
    work: Callable[Params, Awaitable[Result]],
) -> Callable[Params, Coroutine[object, object, Result]]:
    """`_migrate_first` for work that is awaited rather than called."""

    async def with_prepared_database(
        *args: Params.args, **kwargs: Params.kwargs
    ) -> Result:
        prepare_database()
        return await work(*args, **kwargs)

    return with_prepared_database


@dataclass(frozen=True, slots=True)
class _DesktopStores:
    """What a desktop run reads and writes, and the migration they all need."""

    store: SQLiteActivePaperSessions
    history: SQLiteTradeHistory
    prepare: Callable[[], None]


def _bot_actions(
    stores: _DesktopStores,
    *,
    runtime_actions: PaperRuntimeActions,
    runtime_snapshots: RuntimeSnapshotRelay,
    manual_close_price: ManualClosePrice,
    shutdown_runtime: Callable[[], None],
) -> BotActions:
    """Everything the window can ask of the Bot behind it."""
    history = stores.history
    close_paper_position = ClosePaperPosition(
        find_open_basket=history.find_open_basket,
        list_fills=history.list_fills,
        record_closed=history.record_closed_basket,
        record_entry_exit=history.record_entry_exit_fill,
    )

    def retire_session(session: ConfiguredPaperSession) -> None:
        # Asked of the Runtime rather than of the record, because a Session
        # whose worker is still running is one whose last write has not landed.
        if not runtime_actions.new_session_available(session):
            raise RuntimeError("Paper Runtime worker is still active")
        stores.store.retire(session.config.session_id, datetime.now(UTC))

    def close_one_entry(
        session: ConfiguredPaperSession, entry_number: int, *, price: Decimal
    ) -> bool:
        """Sell one Entry, asking whichever of the two is holding the Basket.

        A running Runtime owns it, and a sale written past it would be a
        Basket sold twice — once here and once by the next candle to reach a
        target it still thinks it holds. So the Runtime is asked first, and
        only a Runtime that is not holding this Session declines, which leaves
        the Basket where a stopped Bot leaves it: in the database.
        """
        if runtime_actions.close_entry(session, entry_number, price=price):
            return True
        return close_one_entry_at_rest(
            session,
            entry_number,
            trade_history=history,
            symbol_rules=_symbol_rules_for(session),
            preset=_preset_for(session),
            price=price,
            observed_at=datetime.now(UTC),
        )

    def close_position(session: ConfiguredPaperSession) -> None:
        """Sell every open Entry, asking whichever of the two holds the Basket.

        The Entries a Runtime is holding are asked for one at a time, the same
        way a single row already is, so a sale can never be written past the
        Runtime that owns the Basket. A Session with no Runtime holding it at
        all closes straight from the database, one Fill per Entry it finds
        still open.
        """
        entry_numbers = runtime_actions.open_entry_numbers(session)
        if entry_numbers is None:
            close_paper_position.execute(
                session,
                market_price=lambda: manual_close_price.get(session),
                tick_size=_symbol_rules_for(session).tick_size,
            )
            return
        price = manual_close_price.get(session)
        for entry_number in entry_numbers:
            close_one_entry(session, entry_number, price=price)

    def close_entry(session: ConfiguredPaperSession, entry_number: int) -> bool:
        return close_one_entry(
            session, entry_number, price=manual_close_price.get(session)
        )

    def cancel_cooldown(session: ConfiguredPaperSession) -> bool:
        """Lift the Cooldown holding this Basket, wherever the Basket is held.

        A running Runtime holds the live gate a candle reads; asking it
        directly is what makes a waiver open the gate this instant rather
        than on the next candle it re-reads the database on. Only a Runtime
        not holding this Session falls to the database directly, and only
        Spot has a database to fall to: Futures never takes a Basket back,
        so a Futures Cooldown exists only while its Runtime is holding it.
        """
        if runtime_actions.waive_cooldown(session):
            return True
        if session.config.market_type is not MarketType.SPOT:
            return False
        return waive_cooldown_at_rest(
            session,
            trade_history=history,
            symbol_rules=_symbol_rules_for(session),
            preset=_preset_for(session),
            observed_at=datetime.now(UTC),
        )

    def read_open_basket(session: ConfiguredPaperSession) -> ReopenedBasket | None:
        state = read_open_basket_state(history, session.config.session_id)
        if state is None or session.config.market_type is not MarketType.SPOT:
            return None
        preset = _preset_for(session)
        return reopen_basket(
            state,
            entry_policy=session.config.entry_policy,
            take_profit_atr_multiplier=preset.take_profit_atr_multiplier,
            tick_size=_symbol_rules_for(session).tick_size,
        )

    def has_open_position(session: ConfiguredPaperSession) -> bool:
        return history.find_open_basket(session.config.session_id) is not None

    replace_session = ReplacePaperSession(
        stop_bot=runtime_actions.stop_for_replacement,
        has_open_position=has_open_position,
        close_position=close_position,
        retire_session=retire_session,
    )

    return BotActions(
        start=runtime_actions.start,
        stop=runtime_actions.stop,
        initialize=runtime_actions.initialize,
        runtime_snapshots=runtime_snapshots,
        shutdown_runtime=shutdown_runtime,
        replace_session=_migrate_first(stores.prepare, replace_session.execute),
        close_position=_migrate_first(stores.prepare, close_position),
        close_entry=_migrate_first(stores.prepare, close_entry),
        cancel_cooldown=_migrate_first(stores.prepare, cancel_cooldown),
        has_open_position=has_open_position,
        read_open_basket=_migrate_first(stores.prepare, read_open_basket),
    )


# Never persisted, and reserved rather than random, so it can never collide
# with a real Session's id.
_CHART_PREVIEW_SESSION_ID = UUID(int=0)


def _default_chart_session() -> ConfiguredPaperSession:
    """A throwaway Session standing in for 'no Bot Session yet'.

    Built the same way a real one is, so every invariant a Session carries
    already holds — but `create_active` never touches SQLite, so this is
    never written down and never trades.
    """
    create = CreatePaperSession(
        create_active=lambda session: PaperSessionCreateOutcome(session, True),
        session_ids=lambda: _CHART_PREVIEW_SESSION_ID,
    )
    return create.execute(
        PaperSessionSetupValues(
            market_type="spot",
            symbol=PAPER_SYMBOLS[0],
            timeframe="5m",
            capital_per_entry="1",
            max_entries="2",
            futures_leverage=None,
        )
    ).session


def _chart_sources(
    stores: _DesktopStores,
    *,
    chart_fact_relay: ChartFactRelay,
    chart_feed: ChartOnlyFeedController,
) -> ChartSources:
    """Everything the Chart reads, and the feed that keeps it current."""
    history = stores.history

    async def load_chart_candles(
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> tuple[Candle, ...]:
        async with _public_market_data(session) as source:
            return await source.load_range(
                session.market_data,
                start=chart_range.start,
                end=chart_range.end,
            )

    def list_chart_fills(
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> tuple[TradeFill, ...]:
        """The Pair's marks, which outlive the Session that made them."""
        return history.list_pair_fills(
            session.market_data.symbol,
            session.config.trade_mode,
            session.config.market_type,
            chart_range.start,
            chart_range.end,
        )

    chart_history = ChartHistory(
        load_candles=load_chart_candles,
        list_fills=list_chart_fills,
    )

    async def load_fills(
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> tuple[TradeFill, ...]:
        return list_chart_fills(session, chart_range)

    def select_view(
        session: ConfiguredPaperSession,
        selection: ChartViewSelection,
    ) -> None:
        publisher = chart_fact_relay.new_generation()
        chart_feed.start(
            session,
            publisher.completed_candle,
            publisher.in_progress_candle,
            selection,
            publish_state=publisher.feed_state,
        )

    def start_feed(session: ConfiguredPaperSession) -> None:
        select_view(
            session,
            ChartViewSelection(
                symbol=session.market_data.symbol,
                timeframe=session.market_data.timeframe,
            ),
        )

    return ChartSources(
        load=_migrate_first_async(stores.prepare, chart_history.load),
        refresh=_migrate_first_async(stores.prepare, chart_history.refresh_completed),
        load_fills=_migrate_first_async(stores.prepare, load_fills),
        facts=chart_fact_relay,
        start_feed=start_feed,
        select_view=select_view,
        default_session=_default_chart_session(),
    )


def run_desktop(database_path: Path | None = None) -> int:
    configure_decimal_context()
    resolved_database_path = database_path or default_database_path()
    # Beside the database, because the two answer the same question together:
    # what the Bot did, and why it stopped doing it.
    configure_desktop_logging(resolved_database_path.parent)
    installation = InstallationLock(resolved_database_path)
    activation_name = activation_name_for(resolved_database_path)
    if not installation.acquire():
        return _hand_over_to_the_running_window(activation_name)
    try:
        return _run_desktop_holding_the_installation(
            resolved_database_path,
            activation_name,
        )
    finally:
        installation.release()


def _hand_over_to_the_running_window(activation_name: str) -> int:
    """Give this launch to the window that is already open, if there is one.

    One installation runs one Bot Session, so a trader launching the app again
    is asking for their window, not for a second one. The lock has already
    refused this process the installation; all that is left is to point the
    ask at whoever holds it and leave.

    Retried because the two events race at the one moment it matters — a
    trader double-clicking the icon can reach this before the first process
    has finished starting, and that first process would then hold the
    installation while answering nothing.

    Nobody answering at all is a different situation, and the one the message
    below was written for: the installation is taken and there is no window to
    send the trader to. It stays an error, exit code and all.
    """
    the_qt_application()
    activation = InstanceActivation(activation_name)
    for attempt in range(_ACTIVATION_ATTEMPTS):
        if activation.ask_the_running_window(timeout_ms=_ACTIVATION_TIMEOUT_MS):
            _logger.info(
                "handed this launch to the window already open",
                extra={"event_name": "desktop.activation.handed_over"},
            )
            return 0
        if attempt + 1 < _ACTIVATION_ATTEMPTS:
            sleep(_ACTIVATION_RETRY_WAIT_SECONDS)
    _logger.error(
        "another process is already running this installation",
        extra={"event_name": "desktop.installation.in_use"},
    )
    report_installation_in_use(_INSTALLATION_IN_USE_MESSAGE)
    return _INSTALLATION_IN_USE_EXIT_CODE


def _run_desktop_holding_the_installation(
    resolved_database_path: Path,
    activation_name: str,
) -> int:
    """Everything that may only run while this process owns the database.

    Split out so the claim reads as the precondition it is: the body below
    reads and writes durable state, and recovers a lifecycle marker on the
    strength of nothing else being able to be running.
    """
    database = SQLiteDatabase(resolved_database_path)
    store = SQLiteActivePaperSessions(database)
    history = SQLiteTradeHistory(database)
    latest_market_price = LatestMarketPrice()
    database_preparation_lock = Lock()

    def prepare_database() -> None:
        with database_preparation_lock:
            resolved_database_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                database.migrate()
            except UnsupportedDatabaseSchemaError as error:
                raise DatabaseCompatibilityError from error

    stores = _DesktopStores(store=store, history=history, prepare=prepare_database)

    async def load_latest_price(session: ConfiguredPaperSession) -> Decimal:
        async with _public_market_data(session) as source:
            return await source.load_latest_price(session.market_data)

    manual_close_price = ManualClosePrice(
        observed=latest_market_price,
        quote=lambda session: asyncio.run(load_latest_price(session)),
        max_age=_MANUAL_CLOSE_PRICE_MAX_AGE,
    )
    create_session = CreatePaperSession(create_active=store.create)

    runtime_snapshots = RuntimeSnapshotRelay()
    chart_fact_relay = ChartFactRelay()
    chart_feed = ChartOnlyFeedController(
        source_factory=BinancePublicMarketData,
        latest_market_price=latest_market_price,
    )
    runtime_actions = PaperRuntimeActions(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=history,
        runtime_snapshots=runtime_snapshots,
        prepare_database=prepare_database,
        symbol_rules=_symbol_rules_for,
        source_factory=BinancePublicMarketData,
        notifier_factory=line_session_notifier,
    )

    def shutdown_desktop_runtimes() -> None:
        chart_feed.close()
        runtime_actions.shutdown()

    return run_desktop_ui(
        create_session=_migrate_first(prepare_database, create_session.execute),
        load_active=_migrate_first(prepare_database, store.get_active),
        list_basket_history=_migrate_first(
            prepare_database, history.list_basket_history
        ),
        list_trade_fills=_migrate_first(prepare_database, history.list_trade_fills),
        read_total_realized_pnl=_migrate_first(
            prepare_database, history.total_realized_pnl
        ),
        validate_session=validate_paper_session,
        bot=_bot_actions(
            stores,
            runtime_actions=runtime_actions,
            runtime_snapshots=runtime_snapshots,
            manual_close_price=manual_close_price,
            shutdown_runtime=shutdown_desktop_runtimes,
        ),
        chart=_chart_sources(
            stores,
            chart_fact_relay=chart_fact_relay,
            chart_feed=chart_feed,
        ),
        clear_database=_migrate_first(prepare_database, database.clear_all_data),
        save_line_notifications=_keep_line_notifications,
        read_line_notifications=line_notification_reading,
        activation_name=activation_name,
    )


def _keep_line_notifications(
    enabled: bool,
    target_id: str,
    access_token: str,
) -> None:
    save_line_notification_settings(
        enabled=enabled,
        target_id=target_id,
        channel_access_token=access_token,
    )


def default_database_path(
    platform: str = sys.platform,
    environment: Mapping[str, str] = os.environ,
) -> Path:
    """The one file a trader's history lives in, named for their machine.

    Each platform has a place it keeps application state, and it is the place
    its backup tools and its users already look. macOS says Application
    Support; Windows says LOCALAPPDATA; Linux says XDG_DATA_HOME. The
    arguments exist so a machine can answer for the others under test —
    nothing calls this with them.
    """
    return _application_directory(platform, environment) / DATABASE_FILE_NAME


def _application_directory(platform: str, environment: Mapping[str, str]) -> Path:
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APPLICATION_NAME
    if platform == "win32":
        # Every supported Windows sets LOCALAPPDATA. Missing, the usual
        # location is still a better answer than a relative path, which would
        # put a trader's history wherever the app happened to be started from.
        local_app_data = environment.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            return Path.home() / "AppData" / "Local" / APPLICATION_NAME
        return Path(local_app_data) / APPLICATION_NAME
    # Linux and every other POSIX desktop. The XDG base directory spec names
    # `~/.local/share` when the variable is unset, and only an absolute value
    # counts — the spec says a relative one is to be ignored.
    data_home = environment.get("XDG_DATA_HOME", "").strip()
    if not data_home or not Path(data_home).is_absolute():
        return Path.home() / ".local" / "share" / APPLICATION_NAME
    return Path(data_home) / APPLICATION_NAME


def database_path_from_environment() -> Path | None:
    """A database for trying things out, kept away from the installed one.

    `default_database_path()` answers for the machine, not for the process, so
    running from a checkout opened the very file a trader's history lives in —
    a session opened while developing landed in the record that is supposed to
    be the trader's own. This lets a checkout say otherwise; nothing else does,
    and the delivered app never asks (see packaging/entry_point.py), so an
    exported variable cannot move a trader's database out from under them.

    `None` means "no answer here" — the caller falls back to the installed one.
    """
    configured = os.environ.get("TIEWTRADE_DATABASE_PATH", "").strip()
    if not configured:
        return None
    # The app changes working directory, so a relative path would name a
    # different file later in the same run than it did at startup.
    return Path(configured).expanduser().resolve()


if __name__ == "__main__":
    raise SystemExit(run_desktop(database_path_from_environment()))
