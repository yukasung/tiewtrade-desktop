from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    ChartSnapshot,
)
from tiewtrade.application.chart_history import ChartHistory
from tiewtrade.application.chart_realtime_feed import ChartOnlyFeedController
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.database_compatibility import DatabaseCompatibilityError
from tiewtrade.application.latest_market_price import LatestMarketPrice
from tiewtrade.application.paper_manual_close import ClosePaperPosition
from tiewtrade.application.paper_runtime import PaperRuntimeController
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
    validate_paper_session,
)
from tiewtrade.application.paper_symbols import paper_symbol_rules
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.decimal_context import configure_decimal_context
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


def run_desktop(database_path: Path | None = None) -> int:
    configure_decimal_context()
    resolved_database_path = database_path or default_database_path()
    database = SQLiteDatabase(resolved_database_path)
    store = SQLiteActivePaperSessions(database)
    history = SQLiteTradeHistory(database)
    latest_market_price = LatestMarketPrice()
    close_paper_position = ClosePaperPosition(
        find_open_basket=history.find_open_basket,
        list_fills=history.list_fills,
        record_closed=history.record_closed_basket,
    )
    create_session = CreatePaperSession(create_active=store.create)
    database_preparation_lock = Lock()

    def prepare_database() -> None:
        with database_preparation_lock:
            resolved_database_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                database.migrate()
            except UnsupportedDatabaseSchemaError as error:
                raise DatabaseCompatibilityError from error

    def create_after_migration(
        values: PaperSessionSetupValues,
    ) -> PaperSessionCreateOutcome:
        prepare_database()
        return create_session.execute(values)

    def load_after_migration() -> ConfiguredPaperSession | None:
        prepare_database()
        return store.get_active()

    def retire_after_migration(session: ConfiguredPaperSession) -> None:
        prepare_database()
        if not runtime_actions.new_session_available(session):
            raise RuntimeError("Paper Runtime worker is still active")
        store.retire(session.config.session_id, datetime.now(UTC))

    def close_position_after_migration(session: ConfiguredPaperSession) -> None:
        prepare_database()
        close_paper_position.execute(
            session,
            market_price=lambda: latest_market_price.get(
                session.config.session_id,
                max_age=_MANUAL_CLOSE_PRICE_MAX_AGE,
            ),
            tick_size=_symbol_rules_for(session).tick_size,
        )

    def list_trade_fills_after_migration(
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        prepare_database()
        return history.list_trade_fills(filters)

    def list_basket_history_after_migration(
        filters: TradeHistoryFilter,
    ) -> BasketHistoryResult:
        prepare_database()
        return history.list_basket_history(filters)

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

    async def load_chart_candles(
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> tuple[Candle, ...]:
        source = BinancePublicMarketData(
            BinancePublicEndpoints.for_market_type(session.config.market_type)
        )
        try:
            return await source.load_range(
                session.market_data,
                start=chart_range.start,
                end=chart_range.end,
            )
        finally:
            await source.close()

    def list_chart_fills(
        session_id: UUID,
        chart_range: ChartRange,
    ) -> tuple[TradeFill, ...]:
        return history.list_session_fills(
            session_id,
            chart_range.start,
            chart_range.end,
        )

    chart_history = ChartHistory(
        load_candles=load_chart_candles,
        list_fills=list_chart_fills,
    )

    async def load_chart(
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        prepare_database()
        return await chart_history.load(session, chart_range)

    async def refresh_chart(
        session: ConfiguredPaperSession,
        snapshot: ChartSnapshot,
        candle: Candle,
    ) -> ChartSnapshot:
        prepare_database()
        return await chart_history.refresh_completed(session, snapshot, candle)

    async def load_chart_fills(
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> tuple[TradeFill, ...]:
        prepare_database()
        return list_chart_fills(session.config.session_id, chart_range)

    def start_chart_feed(session: ConfiguredPaperSession) -> None:
        select_chart_view(
            session,
            ChartViewSelection(
                symbol=session.market_data.symbol,
                timeframe=session.market_data.timeframe,
            ),
        )

    def select_chart_view(
        session: ConfiguredPaperSession,
        selection: ChartViewSelection,
    ) -> None:
        publisher = chart_fact_relay.new_generation()
        chart_feed.start(
            session,
            publisher.completed_candle,
            publisher.in_progress_candle,
            selection,
        )

    def shutdown_desktop_runtimes() -> None:
        chart_feed.close()
        runtime_actions.shutdown()

    return run_desktop_ui(
        create_session=create_after_migration,
        load_active=load_after_migration,
        list_basket_history=list_basket_history_after_migration,
        list_trade_fills=list_trade_fills_after_migration,
        validate_session=validate_paper_session,
        bot=BotActions(
            start=runtime_actions.start,
            stop=runtime_actions.stop,
            initialize=runtime_actions.initialize,
            runtime_snapshots=runtime_snapshots,
            shutdown_runtime=shutdown_desktop_runtimes,
            stop_for_replacement=runtime_actions.stop_for_replacement,
            close_position=close_position_after_migration,
            has_open_position=lambda session: (
                history.find_open_basket(session.config.session_id) is not None
            ),
            retire_session=retire_after_migration,
        ),
        chart=ChartSources(
            load=load_chart,
            refresh=refresh_chart,
            load_fills=load_chart_fills,
            facts=chart_fact_relay,
            start_feed=start_chart_feed,
            select_view=select_chart_view,
        ),
    )


def default_database_path() -> Path:
    directory = Path.home() / "Library" / "Application Support" / "TiewTrade"
    return directory / "tiewtrade.sqlite3"


if __name__ == "__main__":
    raise SystemExit(run_desktop())
