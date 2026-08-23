import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock
from uuid import UUID

from tiewtrade.application.bot_control import (
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
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
from tiewtrade.application.paper_manual_close import ClosePaperPosition
from tiewtrade.application.paper_runtime import (
    PaperRuntimeController,
    read_open_basket_state,
)
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
    validate_paper_session,
)
from tiewtrade.application.paper_session_sink import (
    close_one_entry_at_rest,
    waive_cooldown_at_rest,
)
from tiewtrade.application.paper_symbols import PAPER_SYMBOLS, paper_symbol_rules
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.decimal_context import configure_decimal_context
from tiewtrade.desktop_logging import configure_desktop_logging
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.binance.public_market_data import BinancePublicMarketData
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import (
    SQLiteDatabase,
    UnsupportedDatabaseSchemaError,
)
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import TradeFill
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay
from tiewtrade.ui.chart_workflow import ChartFactRelay
from tiewtrade.ui.desktop import run_desktop as run_desktop_ui
from tiewtrade.ui.main_window import BotActions, ChartSources

_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS = 30.0
_MANUAL_CLOSE_PRICE_MAX_AGE = timedelta(seconds=30)


class _PaperRuntimeActions:
    def __init__(
        self,
        *,
        lifecycle: SQLitePaperRuntimeLifecycle,
        trade_history: SQLiteTradeHistory,
        runtime_snapshots: RuntimeSnapshotRelay,
        prepare_database: Callable[[], None],
    ) -> None:
        self._lifecycle = lifecycle
        self._trade_history = trade_history
        self._runtime_snapshots = runtime_snapshots
        self._prepare_database = prepare_database
        self._lock = Lock()
        self._controller: PaperRuntimeController | None = None
        self._session: ConfiguredPaperSession | None = None
        self._operation: str | None = None
        self._operation_finished = Event()
        self._operation_finished.set()
        self._shutdown_requested = False
        self._shutdown_stop_claimed = False

    def initialize(self, snapshot: BotControlSnapshot) -> BotLifecycleResult:
        self._prepare_database()
        self._begin_operation("initialize")
        controller: PaperRuntimeController | None = None
        try:
            controller = self._new_controller(snapshot.session)
            with self._lock:
                self._controller = controller
                self._session = snapshot.session
            result = controller.inspect_startup(snapshot.session)
        finally:
            self._finish_operation(
                "initialize",
                controller=controller,
                session=snapshot.session,
            )
        return _lifecycle_result_for_ui(snapshot, result)

    def start(self, snapshot: BotControlSnapshot) -> BotLifecycleResult:
        self._prepare_database()
        self._begin_operation("start")
        controller: PaperRuntimeController | None = None
        try:
            controller = self._new_controller(snapshot.session)
            with self._lock:
                self._controller = controller
                self._session = snapshot.session
            result = controller.start(snapshot.session)
        finally:
            self._finish_operation(
                "start",
                controller=controller,
                session=snapshot.session,
            )
        return _lifecycle_result_for_ui(snapshot, result)

    def stop(self, snapshot: BotControlSnapshot) -> BotLifecycleResult:
        self._begin_operation("stop")
        controller: PaperRuntimeController | None = None
        try:
            controller = self._current_controller(snapshot.session)
            result = controller.stop(snapshot.session)
        finally:
            self._finish_operation(
                "stop",
                controller=controller,
                session=snapshot.session,
            )
        return _lifecycle_result_for_ui(snapshot, result)

    def close_entry(
        self,
        session: ConfiguredPaperSession,
        entry_number: int,
        *,
        price: Decimal,
    ) -> bool:
        """Ask the running Runtime to let one Entry go, if it is holding this one."""
        with self._lock:
            controller = self._controller if self._session == session else None
        if controller is None:
            return False
        return controller.close_entry(session, entry_number, price=price)

    def open_entry_numbers(
        self, session: ConfiguredPaperSession
    ) -> tuple[int, ...] | None:
        """Entry numbers the running Runtime currently holds, if it holds this one.

        None where no Runtime holds this Session — Close All then falls back
        to whatever the database has, the way a single row's close already
        does. A Runtime that holds it but has nothing open answers with an
        empty tuple, not None: there is a position, it is just closed.
        """
        with self._lock:
            controller = self._controller if self._session == session else None
        if controller is None:
            return None
        basket = controller.current_workspace.position_basket.basket
        if basket is None:
            return ()
        return tuple(entry.entry_number for entry in basket.entries)

    def waive_cooldown(self, session: ConfiguredPaperSession) -> bool:
        """Ask the running Runtime to lift a Cooldown it is holding."""
        with self._lock:
            controller = self._controller if self._session == session else None
        if controller is None:
            return False
        return controller.waive_cooldown(session)

    def stop_for_replacement(self, session: ConfiguredPaperSession) -> None:
        with self._lock:
            controller = self._controller if self._session == session else None
        lifecycle = self._lifecycle.read(session.config.session_id)
        if lifecycle is None or lifecycle.state is PaperRuntimeLifecycleState.STOPPED:
            return
        self._begin_operation("replace")
        try:
            if controller is not None and controller.owns_active_runtime(session):
                result = controller.stop(session)
            else:
                if controller is None:
                    controller = self._new_controller(session)
                    with self._lock:
                        self._controller = controller
                        self._session = session
                result = controller.recover(session)
            header = result.workspace.header
            if header is None or header.runtime_state is not BotRuntimeState.STOPPED:
                raise RuntimeError("Paper Bot must stop before it can be replaced")
        finally:
            self._finish_operation("replace", controller=controller, session=session)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_requested = True
            if self._operation is not None:
                operation_finished = self._operation_finished
                controller = None
                session = None
            elif self._shutdown_stop_claimed:
                return
            else:
                self._shutdown_stop_claimed = True
                operation_finished = None
                controller = self._controller
                session = self._session
        if operation_finished is not None:
            operation_finished.wait(_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS)
            return
        self._stop_controller_if_needed(controller, session)

    def new_session_available(self, session: ConfiguredPaperSession) -> bool:
        with self._lock:
            if self._operation is not None:
                return False
            controller = self._controller if self._session == session else None
        return controller is None or not controller.owns_active_runtime(session)

    def _begin_operation(self, operation: str) -> None:
        with self._lock:
            if self._shutdown_requested:
                raise RuntimeError("Paper Runtime is shutting down")
            if self._operation is not None:
                raise RuntimeError("Paper Runtime operation is already in progress")
            self._operation = operation
            self._operation_finished.clear()

    def _finish_operation(
        self,
        operation: str,
        *,
        controller: PaperRuntimeController | None,
        session: ConfiguredPaperSession,
    ) -> None:
        with self._lock:
            shutdown_stop = (
                self._shutdown_requested
                and not self._shutdown_stop_claimed
                and operation != "stop"
            )
            if self._shutdown_requested and not self._shutdown_stop_claimed:
                self._shutdown_stop_claimed = True
        try:
            if shutdown_stop:
                self._stop_controller_if_needed(controller, session)
        finally:
            with self._lock:
                if self._operation == operation:
                    self._operation = None
                self._operation_finished.set()

    def _stop_controller_if_needed(
        self,
        controller: PaperRuntimeController | None,
        session: ConfiguredPaperSession | None,
    ) -> None:
        if controller is None or session is None:
            return
        try:
            result = controller.current_result
        except RuntimeError:
            return
        header = result.workspace.header
        if header is None or header.runtime_state not in {
            BotRuntimeState.RUNNING,
            BotRuntimeState.BLOCKED,
        }:
            return
        controller.stop(session)

    def _new_controller(
        self,
        session: ConfiguredPaperSession,
    ) -> PaperRuntimeController:
        publish = self._runtime_snapshots.new_generation()
        controller: PaperRuntimeController | None = None

        def publish_current_result(workspace: object) -> None:
            del workspace
            if controller is not None:
                publish(controller.current_result)

        controller = PaperRuntimeController(
            lifecycle=self._lifecycle,
            trade_history=self._trade_history,
            symbol_rules=_symbol_rules_for(session),
            source_factory=BinancePublicMarketData,
            snapshot_callback=publish_current_result,
            completed_candle_callback=publish.completed_candle,
            in_progress_candle_callback=publish.in_progress_candle,
        )
        return controller

    def _current_controller(
        self,
        session: ConfiguredPaperSession,
    ) -> PaperRuntimeController:
        with self._lock:
            controller = self._controller
            active_session = self._session
        if controller is None or active_session != session:
            raise RuntimeError("Paper Runtime is not active for this Session")
        return controller


def _lifecycle_result_for_ui(
    current: BotControlSnapshot,
    result: BotLifecycleResult,
) -> BotLifecycleResult:
    header = result.workspace.header
    if header is None:
        raise ValueError("Paper Runtime result requires a header")
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
    runtime_actions: _PaperRuntimeActions,
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
            stop_loss_atr_multiplier=preset.stop_loss_atr_multiplier,
            tick_size=_symbol_rules_for(session).tick_size,
        )

    return BotActions(
        start=runtime_actions.start,
        stop=runtime_actions.stop,
        initialize=runtime_actions.initialize,
        runtime_snapshots=runtime_snapshots,
        shutdown_runtime=shutdown_runtime,
        stop_for_replacement=runtime_actions.stop_for_replacement,
        close_position=_migrate_first(stores.prepare, close_position),
        close_entry=_migrate_first(stores.prepare, close_entry),
        cancel_cooldown=_migrate_first(stores.prepare, cancel_cooldown),
        has_open_position=lambda session: (
            history.find_open_basket(session.config.session_id) is not None
        ),
        retire_session=_migrate_first(stores.prepare, retire_session),
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
    runtime_actions = _PaperRuntimeActions(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=history,
        runtime_snapshots=runtime_snapshots,
        prepare_database=prepare_database,
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
    )


def default_database_path() -> Path:
    directory = Path.home() / "Library" / "Application Support" / "TiewTrade"
    return directory / "tiewtrade.sqlite3"


if __name__ == "__main__":
    raise SystemExit(run_desktop())
