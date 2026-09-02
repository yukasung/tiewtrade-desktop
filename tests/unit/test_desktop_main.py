import asyncio
import signal
import sqlite3
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event, Thread
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest
from pytest import MonkeyPatch

import tiewtrade.desktop_main as desktop_main
import tiewtrade.desktop_paper_runtime as desktop_paper_runtime
import tiewtrade.ui.desktop as ui_desktop
from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tests.support.workspace_snapshots import OBSERVED_AT, basket_snapshot
from tiewtrade.application.bot_control import (
    BotControlAction,
    BotControlSnapshot,
    BotLifecycleResult,
    configured_bot_control,
    workspace_with_runtime_state,
)
from tiewtrade.application.chart_data import ChartRange, ChartSnapshot
from tiewtrade.application.database_compatibility import DatabaseCompatibilityError
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
)
from tiewtrade.application.paper_symbols import paper_symbol_rules
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeHistoryFilter,
)
from tiewtrade.application.trading_workspace import (
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    PositionBasketTabSnapshot,
    TradingWorkspaceSnapshot,
    WorkspaceTabState,
)
from tiewtrade.desktop_logging import LOG_FILE_NAME
from tiewtrade.desktop_paper_runtime import PaperRuntimeActions
from tiewtrade.integrations.binance.public_endpoints import (
    BinanceMarketDataPayloadError,
    BinancePublicEndpoints,
)
from tiewtrade.integrations.binance.public_market_data import BinancePublicMarketData
from tiewtrade.integrations.process_lock.installation_lock import InstallationLock
from tiewtrade.integrations.sqlite.database import (
    SQLiteDatabase,
    UnsupportedDatabaseSchemaError,
)
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.line_notification_composition import line_session_notifier
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.ui.bot_lifecycle_workflow import LifecycleAction, RuntimeSnapshotRelay
from tiewtrade.ui.main_window import BotActions, ChartSources
from tiewtrade.ui.session_workflow import CreateSession, LoadActiveSession
from tiewtrade.ui.theme import DARK_THEME
from tiewtrade.ui.trade_history_workflow import (
    ListBasketHistory,
    ListTradeFills,
)


class _CoordinatedUserVersionCursor:
    def __init__(self, cursor: sqlite3.Cursor, reads: Barrier) -> None:
        self._cursor = cursor
        self._reads = reads

    def fetchone(self) -> sqlite3.Row | tuple[int] | None:
        row = self._cursor.fetchone()
        if row is None:
            return None
        typed_row = cast(sqlite3.Row | tuple[int], row)
        if typed_row[0] == 1:
            try:
                self._reads.wait(timeout=0.5)
            except BrokenBarrierError:
                pass
        return typed_row


class _CoordinatedConnection:
    def __init__(self, connection: sqlite3.Connection, reads: Barrier) -> None:
        self._connection = connection
        self._reads = reads

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> sqlite3.Cursor | _CoordinatedUserVersionCursor:
        cursor = self._connection.execute(sql, parameters)
        if sql.strip().casefold() == "pragma user_version":
            return _CoordinatedUserVersionCursor(cursor, self._reads)
        return cursor

    def __enter__(self) -> "_CoordinatedConnection":
        self._connection.__enter__()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._connection.__exit__(exception_type, exception, traceback)

    def close(self) -> None:
        self._connection.close()


def _create_v1_database(database: SQLiteDatabase) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE basket_results (
                basket_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                trade_mode TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_preset_version TEXT NOT NULL,
                opened_at_utc TEXT NOT NULL,
                closed_at_utc TEXT,
                entry_count INTEGER NOT NULL,
                invested_notional TEXT NOT NULL,
                gross_realized_pnl TEXT NOT NULL,
                trading_fees TEXT NOT NULL,
                funding_fee TEXT NOT NULL,
                net_realized_pnl TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute("PRAGMA user_version = 1")


@pytest.fixture(autouse=True)
def desktop_logging_stays_off(monkeypatch: MonkeyPatch) -> None:
    """No test here may leave a live file handler on the package logger.

    Composing the desktop configures logging for the whole process, which
    outlives the test that did it and would write every later test's events
    into a temporary directory that is already gone.
    """
    monkeypatch.setattr(
        desktop_main,
        "configure_desktop_logging",
        lambda directory: directory / LOG_FILE_NAME,
    )


def _runtime_actions(database: SQLiteDatabase) -> PaperRuntimeActions:
    """The guard, handed stubs for everything a real Runtime would be built with.

    Every test that reaches for it replaces `PaperRuntimeController` outright,
    so what a Runtime would have been built with never gets used.
    """
    return PaperRuntimeActions(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        runtime_snapshots=RuntimeSnapshotRelay(),
        prepare_database=lambda: None,
        symbol_rules=lambda session: paper_symbol_rules(
            session.market_data.symbol, session.config.market_type
        ),
        source_factory=BinancePublicMarketData,
        notifier_factory=line_session_notifier,
    )


def test_desktop_configures_decimal_context_and_logging_before_composition(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[str] = []
    original_database = SQLiteDatabase

    def capture_database(path: Path) -> object:
        events.append("database")
        return original_database(path)

    monkeypatch.setattr(
        desktop_main,
        "configure_decimal_context",
        lambda: events.append("decimal"),
        raising=False,
    )

    def capture_logging(directory: Path) -> Path:
        events.append("logging")
        return directory / "tiewtrade.log"

    monkeypatch.setattr(
        desktop_main,
        "configure_desktop_logging",
        capture_logging,
    )
    monkeypatch.setattr(desktop_main, "SQLiteDatabase", capture_database)
    monkeypatch.setattr(desktop_main, "run_desktop_ui", lambda **kwargs: 0)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    assert events == ["decimal", "logging", "database"]


def test_desktop_composition_supplies_migrated_create_and_load_operations(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def capture_desktop(
        *,
        create_session: object,
        load_active: object,
        **dependencies: object,
    ) -> int:
        captured["create_session"] = create_session
        captured["load_active"] = load_active
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)

    result = desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3")

    assert result == 0
    create_session = cast(CreateSession, captured["create_session"])
    load_active_session = cast(LoadActiveSession, captured["load_active"])
    assert load_active_session() is None
    outcome = create_session(
        PaperSessionSetupValues(
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="5m",
            capital_per_entry="200000",
            max_entries="10",
            futures_leverage=None,
        )
    )

    assert isinstance(outcome, PaperSessionCreateOutcome)
    assert outcome.created is True
    active = load_active_session()
    assert isinstance(active, ConfiguredPaperSession)
    assert active.config.session_id == outcome.session.config.session_id
    has_open_position = cast(BotActions, captured["bot"]).has_open_position
    assert has_open_position is not None
    assert has_open_position(active) is False


def test_desktop_composition_supplies_chart_loader_without_ui_adapter_imports(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    selected_endpoints: list[BinancePublicEndpoints] = []
    sources: list[object] = []

    class FakePublicMarketData:
        def __init__(self, endpoints: BinancePublicEndpoints) -> None:
            selected_endpoints.append(endpoints)
            self.closed = False
            sources.append(self)

        async def load_range(
            self, config: object, *, start: object, end: object
        ) -> tuple[object, ...]:
            del config, start, end
            return ()

        async def close(self) -> None:
            self.closed = True

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)
    monkeypatch.setattr(desktop_main, "BinancePublicMarketData", FakePublicMarketData)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    load_chart = cast(
        Callable[[ConfiguredPaperSession, ChartRange], Awaitable[ChartSnapshot]],
        cast(ChartSources, captured["chart"]).load,
    )
    chart_range = ChartRange(
        datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
        datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
    )

    async def invoke_chart_loader(session: ConfiguredPaperSession) -> ChartSnapshot:
        return await load_chart(session, chart_range)

    spot: ChartSnapshot = asyncio.run(invoke_chart_loader(configured_spot_session()))

    assert isinstance(spot, ChartSnapshot)
    # Binance TH lists no Futures market, so a Futures chart has nothing to
    # read and says so rather than drawing another exchange's prices.
    with pytest.raises(BinanceMarketDataPayloadError):
        asyncio.run(invoke_chart_loader(configured_futures_session()))
    assert selected_endpoints == [
        BinancePublicEndpoints(
            rest_klines_url="https://api.binance.th/api/v1/klines",
            websocket_base_url="wss://nbstream.binance.th/w3w/wsa/stream",
        ),
    ]
    assert all(
        "/api/v3/account" not in item.rest_klines_url for item in selected_endpoints
    )
    assert len(sources) == 1
    assert all(cast(FakePublicMarketData, source).closed for source in sources)

    ui_source = "\n".join(
        path.read_text() for path in Path("src/tiewtrade/ui").glob("*.py")
    )
    assert "tiewtrade.application.chart_history" not in ui_source
    chart_history_source = Path(
        "src/tiewtrade/application/chart_history.py"
    ).read_text()
    assert "tiewtrade.integrations" not in chart_history_source


def test_desktop_composition_supplies_a_chart_preview_session_for_setup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The Setup screen needs something to chart before a Session exists.

    Built the same way a real Session is, so nothing downstream has to know
    it stands in for one that was never created — and never persisted, so
    it never reaches `create_active` for real.
    """
    captured: dict[str, object] = {}

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0

    default_session = cast(ChartSources, captured["chart"]).default_session
    assert isinstance(default_session, ConfiguredPaperSession)
    assert default_session.market_data.symbol == "BTCUSDT"
    assert default_session.market_data.timeframe == "5m"
    assert default_session.config.market_type is MarketType.SPOT
    assert default_session.config.session_id == UUID(int=0)


def test_desktop_composition_supplies_migrated_trade_history_queries(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    migration_calls = 0
    original_migrate = SQLiteDatabase.migrate

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    def record_migrate(database: SQLiteDatabase) -> None:
        nonlocal migration_calls
        migration_calls += 1
        original_migrate(database)

    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)
    monkeypatch.setattr(SQLiteDatabase, "migrate", record_migrate)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    assert migration_calls == 0

    list_baskets = cast(ListBasketHistory, captured["list_basket_history"])
    list_fills = cast(ListTradeFills, captured["list_trade_fills"])
    assert list_baskets(TradeHistoryFilter()).items == ()
    assert migration_calls == 1
    assert list_fills(TradeHistoryFilter()) == ()
    assert migration_calls == 2
    assert list_baskets(TradeHistoryFilter()).items == ()
    assert migration_calls == 3
    read_total = cast(Callable[[], Decimal], captured["read_total_realized_pnl"])
    assert read_total() == Decimal("0")
    assert migration_calls == 4


def test_desktop_composes_concrete_paper_runtime_actions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    controllers: list[object] = []

    class FakePaperRuntimeController:
        def __init__(self, **dependencies: object) -> None:
            self.dependencies = dependencies
            self.snapshot_callback = cast(
                Callable[[object], None], dependencies["snapshot_callback"]
            )
            self.result: BotLifecycleResult | None = None
            self.stop_calls = 0
            controllers.append(self)

        @property
        def current_result(self) -> BotLifecycleResult:
            assert self.result is not None
            return self.result

        def inspect_startup(
            self,
            session: ConfiguredPaperSession,
            *,
            sole_process: bool = False,
        ) -> BotLifecycleResult:
            self.sole_process = sole_process
            workspace = configured_bot_control(
                session,
                observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
            ).workspace
            self.result = BotLifecycleResult(workspace=workspace)
            self.snapshot_callback(workspace)
            return self.result

        def start(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            workspace = workspace_with_runtime_state(
                configured_bot_control(
                    session,
                    observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
                ).workspace,
                BotRuntimeState.RUNNING,
                data_freshness=DataFreshness.FRESH,
            )
            self.result = BotLifecycleResult(workspace=workspace)
            self.snapshot_callback(workspace)
            return self.result

        def stop(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            self.stop_calls += 1
            assert self.result is not None
            self.result = BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    self.result.workspace,
                    BotRuntimeState.STOPPED,
                )
            )
            self.snapshot_callback(self.result.workspace)
            return self.result

        def recover(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            workspace = configured_bot_control(
                session,
                observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
            ).workspace
            self.result = BotLifecycleResult(workspace=workspace)
            self.snapshot_callback(workspace)
            return self.result

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(
        desktop_paper_runtime,
        "PaperRuntimeController",
        FakePaperRuntimeController,
        raising=False,
    )
    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0

    bot = cast(BotActions, captured["bot"])
    initialize_bot = cast(LifecycleAction, bot.initialize)
    start_bot = cast(LifecycleAction, bot.start)
    shutdown_runtime = cast(Callable[[], None], bot.shutdown_runtime)
    session = configured_spot_session()
    start_snapshot = configured_bot_control(
        session,
        observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
    )

    initialized = initialize_bot(start_snapshot)

    assert initialized.workspace.header is not None
    assert initialized.workspace.header.runtime_state is BotRuntimeState.CONFIGURED
    assert len(controllers) == 1

    result = start_bot(start_snapshot)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.RUNNING
    assert len(controllers) == 2
    controller = cast(FakePaperRuntimeController, controllers[1])
    assert isinstance(controller.dependencies["lifecycle"], SQLitePaperRuntimeLifecycle)
    assert isinstance(controller.dependencies["trade_history"], SQLiteTradeHistory)
    rules = controller.dependencies["symbol_rules"]
    assert isinstance(rules, SymbolRules)
    assert rules.symbol == "BTCUSDT"
    assert rules.tick_size == Decimal("0.01")
    assert controller.dependencies["source_factory"] is BinancePublicMarketData
    assert isinstance(bot.runtime_snapshots, RuntimeSnapshotRelay)

    shutdown_runtime()

    assert controller.stop_calls == 1


def test_runtime_shutdown_during_start_stops_controller_after_readiness(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    started = Event()
    release_start = Event()
    controllers: list[object] = []

    class SlowPaperRuntimeController:
        def __init__(self, **dependencies: object) -> None:
            del dependencies
            self.result: BotLifecycleResult | None = None
            self.stop_calls = 0
            controllers.append(self)

        @property
        def current_result(self) -> BotLifecycleResult:
            assert self.result is not None
            return self.result

        def start(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            started.set()
            if not release_start.wait(timeout=2):
                raise TimeoutError("test did not release start")
            self.result = BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    configured_bot_control(
                        session,
                        observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
                    ).workspace,
                    BotRuntimeState.RUNNING,
                    data_freshness=DataFreshness.FRESH,
                )
            )
            return self.result

        def stop(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            self.stop_calls += 1
            assert self.result is not None
            self.result = BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    self.result.workspace,
                    BotRuntimeState.STOPPED,
                )
            )
            return self.result

    monkeypatch.setattr(
        desktop_paper_runtime,
        "PaperRuntimeController",
        SlowPaperRuntimeController,
    )
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    actions = _runtime_actions(database)
    session = configured_spot_session()
    errors: list[BaseException] = []

    def start_runtime() -> None:
        try:
            actions.start(
                configured_bot_control(
                    session,
                    observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
                )
            )
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=start_runtime)
    worker.start()
    assert started.wait(timeout=1)

    shutdown_worker = Thread(target=actions.shutdown)
    shutdown_worker.start()

    controller = cast(SlowPaperRuntimeController, controllers[0])
    assert controller.stop_calls == 0
    assert shutdown_worker.is_alive()

    release_start.set()
    worker.join(timeout=1)
    shutdown_worker.join(timeout=1)

    assert not worker.is_alive()
    assert not shutdown_worker.is_alive()
    assert errors == []
    assert controller.stop_calls == 1


def test_runtime_shutdown_waits_for_active_lifecycle_operation_without_duplicate(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    operation_started = Event()
    release_operation = Event()
    controllers: list[object] = []

    class BlockingPaperRuntimeController:
        def __init__(self, **dependencies: object) -> None:
            del dependencies
            self.result: BotLifecycleResult | None = None
            self.stop_calls = 0
            controllers.append(self)

        @property
        def current_result(self) -> BotLifecycleResult:
            assert self.result is not None
            return self.result

        def start(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            self.result = BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    configured_bot_control(
                        session,
                        observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
                    ).workspace,
                    BotRuntimeState.RUNNING,
                    data_freshness=DataFreshness.FRESH,
                )
            )
            return self.result

        def stop(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            self.stop_calls += 1
            operation_started.set()
            if not release_operation.wait(timeout=2):
                raise TimeoutError("test did not release Stop")
            assert self.result is not None
            self.result = BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    self.result.workspace,
                    BotRuntimeState.STOPPED,
                )
            )
            return self.result

    monkeypatch.setattr(
        desktop_paper_runtime,
        "PaperRuntimeController",
        BlockingPaperRuntimeController,
    )
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    actions = _runtime_actions(database)
    session = configured_spot_session()
    configured = configured_bot_control(
        session,
        observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
    )
    running_result = actions.start(configured)
    running = BotControlSnapshot(
        state=BotRuntimeState.RUNNING,
        session=session,
        workspace=running_result.workspace,
        available_actions=frozenset({BotControlAction.STOP}),
    )
    action_worker = Thread(target=lambda: actions.stop(running))
    action_worker.start()
    assert operation_started.wait(timeout=1)
    shutdown_worker = Thread(target=actions.shutdown)
    shutdown_worker.start()

    assert shutdown_worker.is_alive()

    release_operation.set()
    action_worker.join(timeout=1)
    shutdown_worker.join(timeout=1)

    controller = cast(BlockingPaperRuntimeController, controllers[0])
    assert not action_worker.is_alive()
    assert not shutdown_worker.is_alive()
    assert controller.stop_calls == 1


def test_runtime_symbol_rules_derive_symbol_from_session_market_data() -> None:
    session = configured_spot_session()
    session = replace(
        session,
        market_data=replace(session.market_data, symbol="ETHUSDT"),
    )

    rules = desktop_main._symbol_rules_for(session)

    assert rules.symbol == "ETHUSDT"
    assert rules.tick_size == Decimal("0.01")


def _fake_running_controller(
    workspace: TradingWorkspaceSnapshot,
) -> type[object]:
    class FakePaperRuntimeController:
        def __init__(self, **dependencies: object) -> None:
            del dependencies

        @property
        def current_workspace(self) -> TradingWorkspaceSnapshot:
            return workspace

        def is_holding(self, session: ConfiguredPaperSession) -> bool:
            del session
            return True

        def start(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
            return BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    configured_bot_control(
                        session,
                        observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
                    ).workspace,
                    BotRuntimeState.RUNNING,
                    data_freshness=DataFreshness.FRESH,
                )
            )

    return FakePaperRuntimeController


def _workspace_with_basket(
    session: ConfiguredPaperSession, basket: BasketSnapshot | None
) -> TradingWorkspaceSnapshot:
    base = configured_bot_control(
        session, observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC)
    ).workspace
    position_basket = PositionBasketTabSnapshot(
        state=(
            WorkspaceTabState.READY if basket is not None else WorkspaceTabState.EMPTY
        ),
        basket=basket,
        data_as_of_utc=OBSERVED_AT if basket is not None else None,
    )
    return replace(base, position_basket=position_basket)


def test_open_entry_numbers_reads_the_running_runtime_basket(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    session = configured_spot_session()
    workspace = _workspace_with_basket(session, basket_snapshot())
    monkeypatch.setattr(
        desktop_paper_runtime,
        "PaperRuntimeController",
        _fake_running_controller(workspace),
    )
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    actions = _runtime_actions(database)

    actions.start(
        configured_bot_control(
            session, observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC)
        )
    )

    assert actions.open_entry_numbers(session) == (1, 2)


def test_open_entry_numbers_is_empty_when_the_runtime_holds_no_basket(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    session = configured_spot_session()
    workspace = _workspace_with_basket(session, None)
    monkeypatch.setattr(
        desktop_paper_runtime,
        "PaperRuntimeController",
        _fake_running_controller(workspace),
    )
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    actions = _runtime_actions(database)

    actions.start(
        configured_bot_control(
            session, observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC)
        )
    )

    assert actions.open_entry_numbers(session) == ()


def test_open_entry_numbers_is_none_when_no_runtime_holds_the_session(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    actions = _runtime_actions(database)

    assert actions.open_entry_numbers(session) is None


def test_concurrent_session_load_and_history_query_serialize_v1_migration(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    _create_v1_database(SQLiteDatabase(database_path))
    captured: dict[str, object] = {}
    migration_reads = Barrier(2)
    worker_start = Barrier(2)
    original_connect = SQLiteDatabase.connect

    def coordinated_connect(
        database: SQLiteDatabase,
    ) -> _CoordinatedConnection:
        return _CoordinatedConnection(original_connect(database), migration_reads)

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(SQLiteDatabase, "connect", coordinated_connect)
    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)

    assert desktop_main.run_desktop(database_path) == 0
    load_active = cast(LoadActiveSession, captured["load_active"])
    list_baskets = cast(ListBasketHistory, captured["list_basket_history"])

    def load_session() -> ConfiguredPaperSession | None:
        worker_start.wait()
        return load_active()

    def query_history() -> BasketHistoryResult:
        worker_start.wait()
        return list_baskets(TradeHistoryFilter())

    with ThreadPoolExecutor(max_workers=2) as pool:
        load_result = pool.submit(load_session)
        history_result = pool.submit(query_history)

        assert load_result.result() is None
        assert history_result.result().items == ()

    with sqlite3.connect(database_path) as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        basket_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(basket_results)")
        }

    assert schema_version == 16
    assert "leverage" in basket_columns


def test_failed_database_preparation_can_be_retried_by_later_consumer(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    migration_attempts = 0
    original_migrate = SQLiteDatabase.migrate

    def fail_first_migration(database: SQLiteDatabase) -> None:
        nonlocal migration_attempts
        migration_attempts += 1
        if migration_attempts == 1:
            raise sqlite3.OperationalError("injected migration failure")
        original_migrate(database)

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(SQLiteDatabase, "migrate", fail_first_migration)
    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)
    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    load_active = cast(LoadActiveSession, captured["load_active"])
    list_baskets = cast(ListBasketHistory, captured["list_basket_history"])

    with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
        load_active()

    result = list_baskets(TradeHistoryFilter())

    assert result.items == ()
    assert migration_attempts == 2


def test_newer_schema_is_translated_at_desktop_composition_boundary(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def reject_newer_schema(database: SQLiteDatabase) -> None:
        raise UnsupportedDatabaseSchemaError(
            database_version=4,
            supported_version=3,
        )

    def capture_desktop(**dependencies: object) -> int:
        captured.update(dependencies)
        return 0

    monkeypatch.setattr(SQLiteDatabase, "migrate", reject_newer_schema)
    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)
    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    load_active = cast(LoadActiveSession, captured["load_active"])

    with pytest.raises(DatabaseCompatibilityError) as caught:
        load_active()

    assert isinstance(caught.value.__cause__, UnsupportedDatabaseSchemaError)


def test_ui_desktop_forwards_required_trade_history_dependencies(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeApplication:
        @classmethod
        def instance(cls) -> "FakeApplication":
            return cls()

        def exec(self) -> int:
            return 0

    class FakeMainWindow:
        def __init__(self, **dependencies: object) -> None:
            captured.update(dependencies)

        def setStyleSheet(self, style_sheet: str) -> None:
            captured["style_sheet"] = style_sheet

        def show(self) -> None:
            captured["shown"] = True

    monkeypatch.setattr(ui_desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", FakeMainWindow)
    runtime_snapshots = RuntimeSnapshotRelay()

    def shutdown_runtime() -> None:
        pass

    assert (
        ui_desktop.run_desktop(
            create_session=lambda values: None,  # type: ignore[arg-type,return-value]
            load_active=lambda: None,
            list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
            list_trade_fills=lambda _filters: (),
            bot=BotActions(
                runtime_snapshots=runtime_snapshots,
                shutdown_runtime=shutdown_runtime,
            ),
        )
        == 0
    )
    assert "list_baskets" not in captured
    assert "list_fills" not in captured
    assert callable(captured["list_basket_history"])
    assert callable(captured["list_trade_fills"])
    forwarded_bot = cast(BotActions, captured["bot"])
    assert forwarded_bot.runtime_snapshots is runtime_snapshots
    assert forwarded_bot.shutdown_runtime is shutdown_runtime
    assert captured["style_sheet"] == DARK_THEME
    assert captured["shown"] is True


def test_ui_desktop_forwards_chart_view_selection_callback(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeApplication:
        @classmethod
        def instance(cls) -> "FakeApplication":
            return cls()

        def exec(self) -> int:
            return 0

    class FakeMainWindow:
        def __init__(self, **dependencies: object) -> None:
            captured.update(dependencies)

        def setStyleSheet(self, style_sheet: str) -> None:
            pass

        def show(self) -> None:
            pass

    def select_chart_view(
        session: ConfiguredPaperSession,
        selection: object,
    ) -> None:
        pass

    monkeypatch.setattr(ui_desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", FakeMainWindow)

    assert (
        ui_desktop.run_desktop(
            create_session=lambda values: None,  # type: ignore[arg-type,return-value]
            load_active=lambda: None,
            list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
            list_trade_fills=lambda _filters: (),
            chart=ChartSources(select_view=select_chart_view),
        )
        == 0
    )
    assert cast(ChartSources, captured["chart"]).select_view is select_chart_view


def test_desktop_composition_defers_database_directory_creation_to_load_worker(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Composition still touches no disk — except to claim the installation.

    A lock has to be a file, and this one has to be taken before anything
    reads durable state, so its directory is the one thing made up front. The
    database, its migration and the log file stay deferred to the workers
    that can report a failure to the trader.
    """
    captured: dict[str, object] = {}
    mkdir_calls: list[tuple[Path, int, bool, bool]] = []
    original_mkdir = Path.mkdir

    def capture_desktop(
        *,
        create_session: object,
        load_active: object,
        **dependencies: object,
    ) -> int:
        captured["create_session"] = create_session
        captured["load_active"] = load_active
        return 0

    def record_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        mkdir_calls.append((path, mode, parents, exist_ok))
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    # Each platform names its own installation directory, and the variables
    # that can move it are cleared so this answers under tmp_path wherever the
    # suite runs — the fact under test is which directories are made, not
    # where the platform puts them.
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    installation_directory = desktop_main.default_database_path().parent
    monkeypatch.setattr(Path, "mkdir", record_mkdir)
    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_desktop)

    result = desktop_main.run_desktop()

    assert result == 0
    assert installation_directory.is_relative_to(tmp_path)
    # `parents=True` records the whole chain it walks, so the fact under
    # test is that nothing outside that one directory was made.
    assert mkdir_calls != []
    assert all(
        made == installation_directory or made in installation_directory.parents
        for made, _mode, _parents, _exist_ok in mkdir_calls
    )

    load_active = cast(LoadActiveSession, captured["load_active"])
    assert load_active() is None
    assert (installation_directory, 0o777, True, True) in mkdir_calls


def test_open_entry_numbers_is_none_when_the_bot_was_only_initialized(
    tmp_path: Path,
) -> None:
    """A Session opened but never started is not one a Runtime is holding.

    `initialize` puts a controller in place so the tab can read durable
    lifecycle state, and that controller's workspace carries no Basket until
    a Runtime starts and publishes one. Answering an empty tuple there says
    "held, with nothing open", which is what makes Close All sell nothing at
    all rather than fall back to the database.
    """
    session = configured_spot_session()
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    actions = _runtime_actions(database)

    actions.initialize(
        configured_bot_control(
            session, observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC)
        )
    )

    assert actions.open_entry_numbers(session) is None


def test_desktop_claims_the_installation_before_touching_the_database(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Nothing may read or write the database until this process owns it.

    The claim is what makes a lifecycle marker readable at all: until it is
    held, a marker saying "running" could be a live Bot in another window.
    """
    events: list[str] = []
    original_acquire = InstallationLock.acquire
    original_database = SQLiteDatabase

    def capture_acquire(self: InstallationLock) -> bool:
        events.append("lock")
        return original_acquire(self)

    def capture_database(path: Path) -> object:
        events.append("database")
        return original_database(path)

    monkeypatch.setattr(InstallationLock, "acquire", capture_acquire)
    monkeypatch.setattr(desktop_main, "SQLiteDatabase", capture_database)
    monkeypatch.setattr(desktop_main, "run_desktop_ui", lambda **kwargs: 0)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    assert events == ["lock", "database"]


def test_desktop_refuses_a_second_window_on_one_installation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """One installation, one Bot: the rule the whole session model rests on.

    Two windows on one database would run two Bots on one Session, each
    unaware of the Entries the other opened.
    """
    database_path = tmp_path / "tiewtrade.sqlite3"
    opened: list[object] = []
    said: list[str] = []
    held = InstallationLock(database_path)
    assert held.acquire() is True

    def capture_open(**kwargs: object) -> int:
        opened.append(kwargs)
        return 0

    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_open)
    monkeypatch.setattr(desktop_main, "report_installation_in_use", said.append)

    try:
        result = desktop_main.run_desktop(database_path)
    finally:
        held.release()

    assert result != 0
    assert opened == []
    assert len(said) == 1
    assert not database_path.exists()


def test_desktop_hands_the_installation_back_when_the_window_closes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A closed app must not lock the next one out of its own database."""
    database_path = tmp_path / "tiewtrade.sqlite3"
    monkeypatch.setattr(desktop_main, "run_desktop_ui", lambda **kwargs: 0)

    assert desktop_main.run_desktop(database_path) == 0

    after = InstallationLock(database_path)
    assert after.acquire() is True
    after.release()


def test_desktop_hands_the_installation_back_when_the_window_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A crash on the way up must not leave the installation claimed."""
    database_path = tmp_path / "tiewtrade.sqlite3"

    def fail(**kwargs: object) -> int:
        raise RuntimeError("the window failed to open")

    monkeypatch.setattr(desktop_main, "run_desktop_ui", fail)

    with pytest.raises(RuntimeError):
        desktop_main.run_desktop(database_path)

    after = InstallationLock(database_path)
    assert after.acquire() is True
    after.release()


def test_desktop_startup_inspection_states_that_nothing_else_is_running(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Holding the installation is what licenses recovering a stale marker.

    The controller cannot tell a marker left by a killed Bot from one a live
    Bot is keeping. This composition holds the lock for the life of the app,
    so it is the one place that can say which it is.
    """
    controllers: list[object] = []

    class RecordingController:
        def __init__(self, **dependencies: object) -> None:
            self.sole_process: bool | None = None
            self.result: BotLifecycleResult | None = None
            self.snapshot_callback = cast(
                Callable[[object], None], dependencies["snapshot_callback"]
            )
            controllers.append(self)

        @property
        def current_result(self) -> BotLifecycleResult:
            assert self.result is not None
            return self.result

        def inspect_startup(
            self,
            session: ConfiguredPaperSession,
            *,
            sole_process: bool = False,
        ) -> BotLifecycleResult:
            self.sole_process = sole_process
            workspace = configured_bot_control(
                session,
                observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC),
            ).workspace
            self.result = BotLifecycleResult(workspace=workspace)
            self.snapshot_callback(workspace)
            return self.result

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        desktop_paper_runtime,
        "PaperRuntimeController",
        RecordingController,
        raising=False,
    )

    def capture_open(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(desktop_main, "run_desktop_ui", capture_open)

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0

    bot = cast(BotActions, captured["bot"])
    initialize_bot = cast(LifecycleAction, bot.initialize)
    session = configured_spot_session()
    initialize_bot(
        configured_bot_control(
            session, observed_at_utc=datetime(2026, 8, 2, tzinfo=UTC)
        )
    )

    assert len(controllers) == 1
    assert cast(RecordingController, controllers[0]).sole_process is True


def test_ui_desktop_closes_the_window_on_an_interrupt(
    monkeypatch: MonkeyPatch,
) -> None:
    """Ctrl+C must put the app through the same door the close button does.

    Killed where it stands, the Bot leaves an open WebSocket, a market-data
    runtime stopped out of order and a lifecycle marker still saying it is
    running. `closeEvent` already answers all three, so an interrupt asks for
    it rather than for anything of its own.
    """
    closed: list[str] = []
    handlers: dict[int, object] = {}

    class FakeApplication:
        @classmethod
        def instance(cls) -> "FakeApplication":
            return cls()

        def exec(self) -> int:
            # Standing in for the C++ loop: the interrupt lands while it runs.
            for received in (signal.SIGINT, signal.SIGTERM):
                cast(Callable[[int, object], None], handlers[received])(received, None)
            return 0

    class FakeMainWindow:
        def __init__(self, **dependencies: object) -> None:
            pass

        def setStyleSheet(self, style_sheet: str) -> None:
            pass

        def show(self) -> None:
            pass

        def close(self) -> None:
            closed.append("close")

    def record_handler(received: int, handler: object) -> object:
        previous = handlers.get(received)
        handlers[received] = handler
        return previous

    monkeypatch.setattr(ui_desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", FakeMainWindow)
    monkeypatch.setattr(signal, "signal", record_handler)

    assert (
        ui_desktop.run_desktop(
            create_session=lambda values: None,  # type: ignore[arg-type,return-value]
            load_active=lambda: None,
        )
        == 0
    )
    assert closed == ["close", "close"]


def test_ui_desktop_gives_the_interpreter_time_to_run_a_handler(
    monkeypatch: MonkeyPatch,
) -> None:
    """A handler that never runs is the same as no handler at all.

    Python runs signal handlers between its own bytecodes, and `exec` spends
    its time inside Qt. Something has to hand control back periodically or
    the interrupt sits unnoticed until the next mouse click.
    """
    timers: list[object] = []

    class FakeApplication:
        @classmethod
        def instance(cls) -> "FakeApplication":
            return cls()

        def exec(self) -> int:
            return 0

    class FakeMainWindow:
        def __init__(self, **dependencies: object) -> None:
            pass

        def setStyleSheet(self, style_sheet: str) -> None:
            pass

        def show(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeTimer:
        def __init__(self) -> None:
            self.interval: int | None = None
            self.timeout = _FakeSignal()
            timers.append(self)

        def start(self, interval: int) -> None:
            self.interval = interval

        def stop(self) -> None:
            self.interval = None

    monkeypatch.setattr(ui_desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", FakeMainWindow)
    monkeypatch.setattr(ui_desktop, "QTimer", FakeTimer)
    monkeypatch.setattr(signal, "signal", lambda received, handler: None)

    ui_desktop.run_desktop(
        create_session=lambda values: None,  # type: ignore[arg-type,return-value]
        load_active=lambda: None,
    )

    assert len(timers) == 1
    timer = cast(FakeTimer, timers[0])
    assert timer.timeout.connected != []


def test_ui_desktop_puts_back_the_handlers_it_found(
    monkeypatch: MonkeyPatch,
) -> None:
    """The app is a guest in this interpreter, tests and shells included."""
    installed: list[tuple[signal.Signals, object]] = []
    original: dict[signal.Signals, object] = {
        signal.SIGINT: object(),
        signal.SIGTERM: object(),
    }

    class FakeApplication:
        @classmethod
        def instance(cls) -> "FakeApplication":
            return cls()

        def exec(self) -> int:
            return 0

    class FakeMainWindow:
        def __init__(self, **dependencies: object) -> None:
            pass

        def setStyleSheet(self, style_sheet: str) -> None:
            pass

        def show(self) -> None:
            pass

        def close(self) -> None:
            pass

    def record_handler(received: signal.Signals, handler: object) -> object:
        installed.append((received, handler))
        return original[received]

    monkeypatch.setattr(ui_desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", FakeMainWindow)
    monkeypatch.setattr(signal, "signal", record_handler)

    ui_desktop.run_desktop(
        create_session=lambda values: None,  # type: ignore[arg-type,return-value]
        load_active=lambda: None,
    )

    assert [received for received, _handler in installed[-2:]] == [
        signal.SIGINT,
        signal.SIGTERM,
    ]
    assert [handler for _received, handler in installed[-2:]] == [
        original[signal.SIGINT],
        original[signal.SIGTERM],
    ]


def test_ui_desktop_opens_where_signals_cannot_be_handled(
    monkeypatch: MonkeyPatch,
) -> None:
    """Only the main thread may install handlers, and the app still has to run.

    A window that refused to open because it could not register for Ctrl+C
    would be trading tidiness for the whole application.
    """

    class FakeApplication:
        @classmethod
        def instance(cls) -> "FakeApplication":
            return cls()

        def exec(self) -> int:
            return 0

    class FakeMainWindow:
        def __init__(self, **dependencies: object) -> None:
            pass

        def setStyleSheet(self, style_sheet: str) -> None:
            pass

        def show(self) -> None:
            pass

        def close(self) -> None:
            pass

    def refuse(received: int, handler: object) -> object:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(ui_desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(ui_desktop, "MainWindow", FakeMainWindow)
    monkeypatch.setattr(signal, "signal", refuse)

    assert (
        ui_desktop.run_desktop(
            create_session=lambda values: None,  # type: ignore[arg-type,return-value]
            load_active=lambda: None,
        )
        == 0
    )


class _FakeSignal:
    def __init__(self) -> None:
        self.connected: list[object] = []

    def connect(self, slot: object) -> None:
        self.connected.append(slot)


def test_a_development_database_is_chosen_by_the_environment(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Development writes somewhere a trader's own history cannot be reached.

    One path served both, so `make run` and the delivered app opened the same
    file: a session opened while trying something out landed in the record a
    trader keeps.
    """
    development_database = tmp_path / "development" / "tiewtrade.sqlite3"
    monkeypatch.setenv("TIEWTRADE_DATABASE_PATH", str(development_database))

    assert desktop_main.database_path_from_environment() == development_database


def test_without_the_variable_the_installed_database_is_the_answer(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIEWTRADE_DATABASE_PATH", raising=False)

    assert desktop_main.database_path_from_environment() is None


def test_an_empty_variable_is_not_a_path(monkeypatch: MonkeyPatch) -> None:
    """A variable exported but left blank means "unset", not the current directory."""
    monkeypatch.setenv("TIEWTRADE_DATABASE_PATH", "")

    assert desktop_main.database_path_from_environment() is None


def test_the_development_database_is_resolved_against_no_working_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A relative path follows the process, and the app changes directory.

    Left relative, the same `make run-dev` would open a different file
    depending on where it was run from.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TIEWTRADE_DATABASE_PATH", ".local/tiewtrade.sqlite3")

    resolved = desktop_main.database_path_from_environment()

    assert resolved is not None
    assert resolved.is_absolute()
    assert resolved == tmp_path / ".local" / "tiewtrade.sqlite3"


def test_the_installed_database_lives_in_application_support_on_macos() -> None:
    installed = desktop_main.default_database_path(platform="darwin", environment={})

    assert installed == (
        Path.home()
        / "Library"
        / "Application Support"
        / "TiewTrade"
        / desktop_main.DATABASE_FILE_NAME
    )


def test_the_installed_database_lives_in_local_app_data_on_windows(
    tmp_path: Path,
) -> None:
    """Windows keeps per-machine application state in LOCALAPPDATA.

    A database written under `Library/Application Support` on Windows is a
    folder no Windows user looks in and no backup tool treats as application
    state.
    """
    installed = desktop_main.default_database_path(
        platform="win32",
        environment={"LOCALAPPDATA": str(tmp_path)},
    )

    assert installed == tmp_path / "TiewTrade" / desktop_main.DATABASE_FILE_NAME


def test_windows_without_the_variable_still_names_the_usual_location() -> None:
    """LOCALAPPDATA is set on every supported Windows, but a missing one must
    not silently put a trader's history in the working directory."""
    installed = desktop_main.default_database_path(platform="win32", environment={})

    assert installed == (
        Path.home()
        / "AppData"
        / "Local"
        / "TiewTrade"
        / desktop_main.DATABASE_FILE_NAME
    )


def test_the_installed_database_lives_in_the_xdg_data_directory_on_linux(
    tmp_path: Path,
) -> None:
    """Linux keeps per-user application state under XDG_DATA_HOME.

    `Library/Application Support` does not exist on Linux, so the fallback
    that answered for every non-Windows platform made the app build that
    folder in a trader's home — a place no Linux backup tool treats as
    application state.
    """
    installed = desktop_main.default_database_path(
        platform="linux",
        environment={"XDG_DATA_HOME": str(tmp_path)},
    )

    assert installed == tmp_path / "TiewTrade" / desktop_main.DATABASE_FILE_NAME


def test_linux_without_the_variable_still_names_the_usual_location() -> None:
    """XDG_DATA_HOME is unset on plenty of desktops; the spec's own default
    is `~/.local/share`, not the working directory."""
    installed = desktop_main.default_database_path(platform="linux", environment={})

    assert installed == (
        Path.home() / ".local" / "share" / "TiewTrade" / desktop_main.DATABASE_FILE_NAME
    )
