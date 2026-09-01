"""The one Paper Runtime this installation has, and the guard over it.

One installation runs one Bot Session at a time, which is a rule about the
whole machine rather than about any Session: two operations reaching the
Runtime at once is how a Bot ends up half-started, or stopped by one caller
while another is still handing it candles. So every way in goes through the
same claim — one operation at a time, and none at all once a shutdown has been
asked for — and a shutdown that arrives mid-operation is picked up by whoever
is holding the claim rather than waiting for it.

Which market data source and which notifier a Runtime is built with are not
decided here. They are handed in, because they are the composition's to choose
and this is only the thing that keeps two hands off one Runtime.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from threading import Event, Lock

from tiewtrade.application.bot_control import (
    BotControlSnapshot,
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.paper_runtime import PaperRuntimeController
from tiewtrade.application.paper_session_replacement import BotWillNotStopError
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.session_notice import SessionNotifier
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle_source import MarketDataCandleSource
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.ui.bot_lifecycle_workflow import RuntimeSnapshotRelay

SymbolRulesFor = Callable[[ConfiguredPaperSession], SymbolRules]
SourceFactory = Callable[[BinancePublicEndpoints], MarketDataCandleSource]
NotifierFactory = Callable[[], AbstractAsyncContextManager[SessionNotifier]]

# How long a shutdown waits on an operation already running before it stops
# the Runtime itself.
_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS = 30.0


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


class PaperRuntimeActions:
    def __init__(
        self,
        *,
        lifecycle: SQLitePaperRuntimeLifecycle,
        trade_history: SQLiteTradeHistory,
        runtime_snapshots: RuntimeSnapshotRelay,
        prepare_database: Callable[[], None],
        symbol_rules: SymbolRulesFor,
        source_factory: SourceFactory,
        notifier_factory: NotifierFactory,
    ) -> None:
        self._lifecycle = lifecycle
        self._trade_history = trade_history
        self._runtime_snapshots = runtime_snapshots
        self._prepare_database = prepare_database
        self._symbol_rules = symbol_rules
        self._source_factory = source_factory
        self._notifier_factory = notifier_factory
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
            # The composition root holds the installation lock for the life
            # of the app, so nothing else can be running this Session — which
            # is what lets a marker left by a killed Bot be read as stale.
            result = controller.inspect_startup(snapshot.session, sole_process=True)
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

        Whether one holds it is the controller's own answer rather than a
        reading of its workspace. A controller is put in place to inspect
        durable lifecycle state before any Bot runs, and its workspace
        carries no Basket until a running Runtime publishes one — read that
        way, a stopped Bot says "nothing open" about a Position that is
        sitting in the database, and Close All sells none of it.
        """
        with self._lock:
            controller = self._controller if self._session == session else None
        if controller is None or not controller.is_holding(session):
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
                raise BotWillNotStopError(
                    "Paper Bot must stop before it can be replaced"
                )
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
            symbol_rules=self._symbol_rules(session),
            source_factory=self._source_factory,
            notifier_factory=self._notifier_factory,
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
