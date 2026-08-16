from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, Lock, Thread
from time import monotonic
from typing import Protocol
from uuid import UUID

from tiewtrade.application.bot_control import (
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.open_basket import OpenBasketEntry, OpenBasketState
from tiewtrade.application.paper_basket_projection import (
    OpenBasketPosition,
    futures_basket_snapshot,
    open_basket_position,
    position_after_snapshot,
    position_without_entries,
    spot_basket_snapshot,
)
from tiewtrade.application.paper_futures_session import (
    PaperFuturesSession,
    PaperFuturesSessionSnapshot,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_spot_session import (
    PaperSpotSession,
    PaperSpotSessionSnapshot,
)
from tiewtrade.application.session_persistence import SessionPersistenceCoordinator
from tiewtrade.application.trading_workspace import (
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    TradingWorkspaceSnapshot,
    WorkspaceReadState,
    configured_workspace_snapshot,
    paper_runtime_blocked_workspace_snapshot,
    paper_runtime_workspace_snapshot,
    stale_workspace_snapshot,
)
from tiewtrade.execution.paper_futures import PaperFuturesEntryFill
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.sqlite.paper_futures_history import (
    PaperFuturesHistoryContext,
    PaperFuturesSQLiteHistory,
)
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.persistent_paper_futures_session import (
    create_persistent_paper_futures_session,
)
from tiewtrade.integrations.sqlite.persistent_paper_spot_session import (
    create_persistent_paper_spot_session,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataCandleSource
from tiewtrade.market_data.runtime import (
    AsyncioRuntimeScheduler,
    MarketDataRuntime,
    RuntimeScheduler,
)
from tiewtrade.market_data.runtime_state import (
    MarketDataRuntimeSnapshot,
    MarketDataRuntimeState,
)
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules


class PaperPositionRestoreError(RuntimeError):
    """An open Basket cannot be put back the way it was left."""


_START_BLOCKED_REASON = "Paper Bot could not be started"
_STOP_BLOCKED_REASON = "Paper Bot could not be stopped"
_RECOVERY_BLOCKED_REASON = "Paper Bot recovery failed"
_RECOVERY_REQUIRED_REASON = "Paper Bot recovery required"
_RESTORE_BLOCKED_REASON = "Paper Position could not be restored"
_STOP_TIMEOUT_SECONDS = 30.0


class PaperRuntimeController:
    def __init__(
        self,
        *,
        lifecycle: SQLitePaperRuntimeLifecycle,
        trade_history: SQLiteTradeHistory,
        symbol_rules: SymbolRules,
        source_factory: Callable[[BinancePublicEndpoints], MarketDataCandleSource],
        snapshot_callback: Callable[[TradingWorkspaceSnapshot], None],
        completed_candle_callback: Callable[[Candle], None] | None = None,
        in_progress_candle_callback: Callable[[Candle], None] | None = None,
        scheduler: RuntimeScheduler | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._lifecycle = lifecycle
        self._trade_history = trade_history
        self._symbol_rules = symbol_rules
        self._source_factory = source_factory
        self._snapshot_callback = snapshot_callback
        self._completed_candle_callback = completed_candle_callback
        self._in_progress_candle_callback = in_progress_candle_callback
        self._scheduler = scheduler or AsyncioRuntimeScheduler()
        self._clock = clock
        self._lock = Lock()
        self._entry_lock = Lock()
        self._entries_enabled = Event()
        self._ready = Event()
        self._thread_finished = Event()
        self._shutdown_request_complete = Event()
        self._workspace: TradingWorkspaceSnapshot | None = None
        self._result: BotLifecycleResult | None = None
        self._readiness_result: BotLifecycleResult | None = None
        self._runtime: MarketDataRuntime | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._active_session: ConfiguredPaperSession | None = None
        self._observed_runtime_states: list[MarketDataRuntimeState] = []

    @property
    def current_workspace(self) -> TradingWorkspaceSnapshot:
        with self._lock:
            if self._workspace is None:
                raise RuntimeError("Paper Runtime has not been started")
            return self._workspace

    @property
    def current_result(self) -> BotLifecycleResult:
        with self._lock:
            if self._result is None:
                raise RuntimeError("Paper Runtime has not produced a result")
            return self._result

    @property
    def observed_runtime_states(self) -> tuple[MarketDataRuntimeState, ...]:
        with self._lock:
            return tuple(self._observed_runtime_states)

    def start(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
        self._validate_session(session)
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("Paper Runtime is already started")
            self._workspace = configured_workspace_snapshot(
                session,
                observed_at_utc=self._clock(),
            )
            self._active_session = session
            self._entries_enabled.set()
            self._thread_finished.clear()
            self._shutdown_request_complete.clear()
            thread = Thread(
                target=self._run_owned_runtime,
                args=(session,),
                name=f"paper-runtime-{session.config.session_id}",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        self._ready.wait()
        with self._lock:
            if self._readiness_result is None:
                raise RuntimeError("Paper Runtime readiness was not published")
            return self._readiness_result

    def inspect_startup(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
        """Project durable lifecycle state without starting market data."""
        self._validate_session(session)
        baseline = configured_workspace_snapshot(
            session,
            observed_at_utc=self._clock(),
        )
        self._store_result(BotLifecycleResult(workspace=baseline))
        try:
            marker = self._lifecycle.read(session.config.session_id)
            if marker is None:
                return self._publish_result_or_block(
                    BotLifecycleResult(workspace=baseline),
                    blocked_reason=_RECOVERY_BLOCKED_REASON,
                )
            if marker.session_id != session.config.session_id:
                return self._startup_blocked_result(_RECOVERY_BLOCKED_REASON)
            if marker.state is PaperRuntimeLifecycleState.RUNNING:
                return self._startup_blocked_result(_RECOVERY_REQUIRED_REASON)
            return self._publish_result_or_block(
                BotLifecycleResult(workspace=baseline),
                blocked_reason=_RECOVERY_BLOCKED_REASON,
            )
        except Exception:
            return self._startup_blocked_result(_RECOVERY_BLOCKED_REASON)

    def stop(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
        self._validate_session(session)
        deadline = monotonic() + _STOP_TIMEOUT_SECONDS
        self._entries_enabled.clear()
        if not self._entry_lock.acquire(timeout=_remaining_seconds(deadline)):
            return self._blocked_result(_STOP_BLOCKED_REASON)
        self._entry_lock.release()

        stop_future = None
        try:
            runtime, loop = self._owned_runtime_for(session)
            if loop.is_closed() or not loop.is_running():
                raise RuntimeError("Paper Runtime loop is unavailable")
            stop_coroutine = runtime.stop()
            try:
                stop_future = asyncio.run_coroutine_threadsafe(
                    stop_coroutine,
                    loop,
                )
            except BaseException:
                stop_coroutine.close()
                raise
            try:
                stop_future.result(timeout=_remaining_seconds(deadline))
            finally:
                self._shutdown_request_complete.set()
            if not self._thread_finished.wait(_remaining_seconds(deadline)):
                raise TimeoutError("Paper Runtime stop deadline exceeded")
            self._lifecycle.mark_stopped(
                session.config.session_id,
                self._clock(),
            )
            current = self.current_workspace
            freshness = (
                DataFreshness.STALE
                if current.read_state is WorkspaceReadState.STALE
                else DataFreshness.UNAVAILABLE
            )
            result = BotLifecycleResult(
                workspace=workspace_with_runtime_state(
                    current,
                    BotRuntimeState.STOPPED,
                    data_freshness=freshness,
                )
            )
            return self._publish_result_or_block(
                result,
                blocked_reason=_STOP_BLOCKED_REASON,
            )
        except Exception:
            self._shutdown_request_complete.set()
            if stop_future is not None:
                stop_future.cancel()
            return self._blocked_result(_STOP_BLOCKED_REASON)

    def recover(self, session: ConfiguredPaperSession) -> BotLifecycleResult:
        self._validate_session(session)
        self._entries_enabled.clear()
        baseline = configured_workspace_snapshot(
            session,
            observed_at_utc=self._clock(),
        )
        with self._lock:
            current = self._workspace
        if current is None or not _workspace_matches_session(current, session):
            self._store_result(BotLifecycleResult(workspace=baseline))

        try:
            marker = self._lifecycle.read(session.config.session_id)
            if marker is None or marker.session_id != session.config.session_id:
                return self._blocked_result(_RECOVERY_BLOCKED_REASON)
            if marker.state is PaperRuntimeLifecycleState.STOPPED:
                return self._publish_result_or_block(
                    BotLifecycleResult(workspace=baseline),
                    blocked_reason=_RECOVERY_BLOCKED_REASON,
                )
            if self._owns_active_runtime(session):
                return self._blocked_result(_RECOVERY_BLOCKED_REASON)
            self._lifecycle.mark_stopped(
                session.config.session_id,
                self._clock(),
            )
            stopped_workspace = workspace_with_runtime_state(
                self.current_workspace,
                BotRuntimeState.STOPPED,
            )
            return self._publish_result_or_block(
                BotLifecycleResult(workspace=stopped_workspace),
                blocked_reason=_RECOVERY_BLOCKED_REASON,
            )
        except Exception:
            return self._blocked_result(_RECOVERY_BLOCKED_REASON)

    def _run_owned_runtime(self, session: ConfiguredPaperSession) -> None:
        try:
            asyncio.run(self._run(session))
        except PaperPositionRestoreError:
            self._publish_blocked(readiness=True, reason=_RESTORE_BLOCKED_REASON)
        except BaseException:
            self._publish_blocked(readiness=True)
        finally:
            self._thread_finished.set()

    async def _run(self, session: ConfiguredPaperSession) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
        terminal = asyncio.Event()

        def on_transition(snapshot: MarketDataRuntimeSnapshot) -> None:
            self._on_runtime_transition(session, snapshot)
            if snapshot.state in {
                MarketDataRuntimeState.STOPPED,
                MarketDataRuntimeState.FAILED_CLOSED,
            }:
                terminal.set()

        preset = strategy_definition_for(session.config.preset_version).build_preset()
        sink = self._create_sink(session, preset)
        endpoints = BinancePublicEndpoints.for_market_type(session.config.market_type)
        source = self._source_factory(endpoints)
        runtime = MarketDataRuntime(
            config=session.market_data,
            warm_up_count=preset.minimum_warm_up_candles,
            source=source,
            sink=sink,
            scheduler=self._scheduler,
            on_transition=on_transition,
            on_open_candle=self._publish_in_progress_candle,
        )
        with self._lock:
            self._runtime = runtime
        run_task = asyncio.create_task(runtime.run())
        await run_task
        if not self._entries_enabled.is_set() and not terminal.is_set():
            await terminal.wait()
        while (
            not self._entries_enabled.is_set()
            and not self._shutdown_request_complete.is_set()
        ):
            await asyncio.sleep(0)

    def _create_sink(
        self,
        session: ConfiguredPaperSession,
        preset: StrategyPreset,
    ) -> (
        _PaperRuntimeSink[PaperSpotSessionSnapshot]
        | _PaperRuntimeSink[PaperFuturesSessionSnapshot]
    ):
        if session.config.market_type is MarketType.SPOT:
            open_basket = read_open_basket_state(
                self._trade_history, session.config.session_id
            )
            spot_application = PaperSpotSession(
                session.config,
                session.market_data,
                self._symbol_rules,
                preset,
                open_basket=open_basket,
            )
            spot_history = PaperSpotSQLiteHistory(
                PaperSpotHistoryContext(
                    session_id=session.config.session_id,
                    symbol=session.market_data.symbol,
                    timeframe=session.market_data.timeframe,
                    preset_version=session.config.preset_version,
                ),
                self._trade_history,
            )
            return _PaperRuntimeSink(
                warm_up=_warm_up_of(spot_application),
                persistent=create_persistent_paper_spot_session(
                    spot_application, spot_history
                ),
                basket=spot_basket_snapshot,
                publish=self._publish_candle,
                entry_lock=self._entry_lock,
                entries_enabled=self._entries_enabled.is_set,
                position=open_basket_position(spot_application.open_entries),
            )

        # Paper Futures has no way back into a Basket it did not open — a
        # Liquidation Price is not derived from the Entries alone — so rather
        # than trade beside one, it says so and stops.
        if self._trade_history.find_open_basket(session.config.session_id) is not None:
            raise PaperPositionRestoreError(
                "Paper Futures cannot take back an open Basket"
            )
        futures_application = PaperFuturesSession(
            session.config,
            session.market_data,
            self._symbol_rules,
            preset,
        )
        futures_policy = session.config.futures_policy
        if futures_policy is None:
            raise ValueError("Paper Futures requires futures_policy")
        futures_history = PaperFuturesSQLiteHistory(
            PaperFuturesHistoryContext(
                session_id=session.config.session_id,
                symbol=session.market_data.symbol,
                timeframe=session.market_data.timeframe,
                preset_version=session.config.preset_version,
                leverage=futures_policy.leverage,
            ),
            self._trade_history,
        )
        return _PaperRuntimeSink(
            warm_up=_warm_up_of(futures_application),
            persistent=create_persistent_paper_futures_session(
                futures_application, futures_history
            ),
            basket=futures_basket_snapshot,
            publish=self._publish_candle,
            entry_lock=self._entry_lock,
            entries_enabled=self._entries_enabled.is_set,
        )

    def _on_runtime_transition(
        self,
        session: ConfiguredPaperSession,
        snapshot: MarketDataRuntimeSnapshot,
    ) -> None:
        with self._lock:
            self._observed_runtime_states.append(snapshot.state)
        if snapshot.state is MarketDataRuntimeState.LIVE:
            with self._lock:
                already_ready = self._readiness_result is not None
            if already_ready:
                current = self.current_workspace
                if current.header is not None and (
                    current.header.runtime_state is BotRuntimeState.RUNNING
                    and current.header.data_freshness is DataFreshness.STALE
                ):
                    assert current.data_as_of_utc is not None
                    self._publish_or_block(
                        BotLifecycleResult(
                            workspace=paper_runtime_workspace_snapshot(
                                current,
                                basket=current.basket,
                                observed_at_utc=current.data_as_of_utc,
                            )
                        )
                    )
                return
            try:
                self._lifecycle.mark_running(
                    session.config.session_id,
                    snapshot.transitioned_at,
                )
                workspace = paper_runtime_workspace_snapshot(
                    self.current_workspace,
                    basket=self.current_workspace.basket,
                    observed_at_utc=snapshot.transitioned_at,
                )
                self._publish(BotLifecycleResult(workspace=workspace), readiness=True)
            except Exception:
                try:
                    self._publish_blocked(readiness=True)
                finally:
                    self._request_runtime_stop()
        elif snapshot.state is MarketDataRuntimeState.FAILED_CLOSED:
            self._publish_blocked(readiness=True)
        elif snapshot.state in {
            MarketDataRuntimeState.STALE,
            MarketDataRuntimeState.RECONNECTING,
            MarketDataRuntimeState.BACKFILLING,
            MarketDataRuntimeState.RATE_LIMITED,
        }:
            with self._lock:
                ready = self._readiness_result is not None
            if ready:
                self._publish_or_block(
                    BotLifecycleResult(
                        workspace=stale_workspace_snapshot(self.current_workspace)
                    )
                )

    def _publish_candle(
        self,
        basket: BasketSnapshot | None,
        candle: Candle,
        observed_at_utc: datetime,
    ) -> None:
        self._publish(
            BotLifecycleResult(
                workspace=paper_runtime_workspace_snapshot(
                    self.current_workspace,
                    basket=basket,
                    observed_at_utc=observed_at_utc,
                )
            )
        )
        self._publish_completed_candle(candle)

    def _publish_completed_candle(self, candle: Candle) -> None:
        callback = self._completed_candle_callback
        if callback is None:
            return
        try:
            callback(candle)
        except Exception:
            # Chart/read-model observers cannot make Paper Runtime fail closed.
            pass

    def _publish_in_progress_candle(self, candle: Candle) -> None:
        callback = self._in_progress_candle_callback
        if callback is None:
            return
        try:
            callback(candle)
        except Exception:
            # Chart/read-model observers cannot make Paper Runtime fail closed.
            pass

    def _owned_runtime_for(
        self,
        session: ConfiguredPaperSession,
    ) -> tuple[MarketDataRuntime, asyncio.AbstractEventLoop]:
        with self._lock:
            active_session = self._active_session
            runtime = self._runtime
            loop = self._loop
        if active_session is None or active_session != session:
            raise RuntimeError("Paper Runtime Session does not match")
        if runtime is None or loop is None:
            raise RuntimeError("Paper Runtime is unavailable")
        return runtime, loop

    def _owns_active_runtime(self, session: ConfiguredPaperSession) -> bool:
        with self._lock:
            active_session = self._active_session
            thread = self._thread
        return (
            active_session == session
            and thread is not None
            and not self._thread_finished.is_set()
        )

    def owns_active_runtime(self, session: ConfiguredPaperSession) -> bool:
        """Report whether this process still owns the Session runtime worker."""
        self._validate_session(session)
        return self._owns_active_runtime(session)

    def _publish_blocked(
        self,
        *,
        readiness: bool = False,
        reason: str = _START_BLOCKED_REASON,
    ) -> None:
        try:
            current = self.current_workspace
        except RuntimeError:
            return
        if (
            current.header is not None
            and current.header.runtime_state is BotRuntimeState.BLOCKED
        ):
            workspace = current
        else:
            workspace = paper_runtime_blocked_workspace_snapshot(current)
        with self._lock:
            active_session = self._active_session
        if active_session is not None:
            try:
                self._lifecycle.mark_blocked(
                    active_session.config.session_id,
                    self._clock(),
                )
            except Exception:
                pass
        result = BotLifecycleResult(
            workspace=workspace,
            blocked_reason=reason,
        )
        try:
            self._publish(result, readiness=readiness)
        except Exception:
            self._store_result(result)
            if readiness:
                self._latch_readiness(result)

    def _blocked_result(self, reason: str) -> BotLifecycleResult:
        self._publish_blocked(reason=reason)
        return self.current_result

    def _startup_blocked_result(self, reason: str) -> BotLifecycleResult:
        result = BotLifecycleResult(
            workspace=workspace_with_runtime_state(
                self.current_workspace,
                BotRuntimeState.BLOCKED,
            ),
            blocked_reason=reason,
        )
        return self._publish_result_or_block(
            result,
            blocked_reason=_RECOVERY_BLOCKED_REASON,
        )

    def _publish_result_or_block(
        self,
        result: BotLifecycleResult,
        *,
        blocked_reason: str,
    ) -> BotLifecycleResult:
        try:
            self._publish(result)
        except Exception:
            self._publish_blocked(reason=blocked_reason)
        return self.current_result

    def _request_runtime_stop(self) -> None:
        with self._lock:
            runtime = self._runtime
        if runtime is None:
            return
        self._entries_enabled.clear()
        task = asyncio.get_running_loop().create_task(runtime.stop())
        task.add_done_callback(self._internal_stop_finished)

    def _internal_stop_finished(self, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        self._shutdown_request_complete.set()

    def _publish_or_block(self, result: BotLifecycleResult) -> None:
        try:
            self._publish(result)
        except Exception:
            try:
                self._publish_blocked()
            finally:
                self._request_runtime_stop()

    def _publish(
        self,
        result: BotLifecycleResult,
        *,
        readiness: bool = False,
    ) -> None:
        self._store_result(result)
        self._snapshot_callback(result.workspace)
        if readiness:
            self._latch_readiness(result)

    def _store_result(self, result: BotLifecycleResult) -> None:
        with self._lock:
            self._workspace = result.workspace
            self._result = result

    def _latch_readiness(self, result: BotLifecycleResult) -> None:
        with self._lock:
            if self._readiness_result is None:
                self._readiness_result = result
            else:
                return
        self._ready.set()

    def _validate_session(self, session: ConfiguredPaperSession) -> None:
        if not isinstance(session, ConfiguredPaperSession):
            raise ValueError("session must be a ConfiguredPaperSession")
        if session.config.trade_mode is not TradeMode.PAPER:
            raise ValueError("Paper Runtime requires TradeMode.PAPER")
        if session.market_data.symbol != self._symbol_rules.symbol:
            raise ValueError("Paper Runtime SymbolRules must match Session symbol")


class _CandleSnapshot(Protocol):
    """What a Session's snapshot tells the sink, whichever market wrote it."""

    @property
    def accepted(self) -> bool: ...

    @property
    def entry_fill(self) -> PaperSpotEntryFill | PaperFuturesEntryFill | None: ...

    @property
    def basket_entry_count(self) -> int: ...

    @property
    def entry_number(self) -> int | None: ...

    @property
    def entry_take_profit_price(self) -> Decimal | None: ...

    @property
    def sold_entry_numbers(self) -> tuple[int, ...]: ...


class _PaperRuntimeSink[Snapshot: _CandleSnapshot]:
    """Feeds candles to one Paper Session and publishes what it decided.

    Spot and Futures differ in what a Basket looks like, not in how a candle
    reaches one, so the market is a reading handed in rather than a class of
    its own: how to warm up, how to persist, and how to read a Basket out of
    the snapshot that comes back.
    """

    def __init__(
        self,
        *,
        warm_up: Callable[[tuple[Candle, ...], datetime], None],
        persistent: SessionPersistenceCoordinator[Snapshot],
        basket: Callable[
            [Snapshot, OpenBasketPosition | None, Candle, datetime],
            BasketSnapshot | None,
        ],
        publish: Callable[[BasketSnapshot | None, Candle, datetime], None],
        entry_lock: Lock,
        entries_enabled: Callable[[], bool],
        position: OpenBasketPosition | None = None,
    ) -> None:
        self._warm_up = warm_up
        self._persistent = persistent
        self._basket = basket
        self._publish = publish
        self._entry_lock = entry_lock
        self._entries_enabled = entries_enabled
        # A Basket taken back arrives with its Entries already filled, so the
        # position starts where they left it rather than at nothing.
        self._position = position

    async def warm_up(
        self,
        candles: tuple[Candle, ...],
        *,
        received_at: datetime,
    ) -> None:
        self._warm_up(candles, received_at)

    async def process_completed(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> None:
        with self._entry_lock:
            if not self._entries_enabled():
                return
            snapshot = self._persistent.process_completed_candle(
                candle, received_at=received_at
            ).session
            self._publish_snapshot(snapshot, candle, candle.close_time)

    async def process_in_progress(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> None:
        """Close the Basket the moment the forming candle reaches its exit.

        Whether a market acts between candles is the Session's rule to state,
        not this sink's: Paper Futures answers an unaccepted snapshot here and
        so passes through without publishing.
        """
        with self._entry_lock:
            if not self._entries_enabled():
                return
            snapshot = self._persistent.process_in_progress_candle(
                candle, observed_at=received_at
            ).session
            if not snapshot.accepted:
                return
            self._publish_snapshot(snapshot, candle, received_at)

    def _publish_snapshot(
        self,
        snapshot: Snapshot,
        candle: Candle,
        observed_at_utc: datetime,
    ) -> None:
        self._position = position_after_snapshot(
            position_without_entries(self._position, snapshot.sold_entry_numbers),
            entry_fill=snapshot.entry_fill,
            basket_entry_count=snapshot.basket_entry_count,
            entry_number=snapshot.entry_number,
            take_profit_price=snapshot.entry_take_profit_price,
        )
        if self._entries_enabled():
            self._publish(
                self._basket(snapshot, self._position, candle, observed_at_utc),
                candle,
                observed_at_utc,
            )


def _warm_up_of(
    session: PaperSpotSession | PaperFuturesSession,
) -> Callable[[tuple[Candle, ...], datetime], None]:
    """The one thing the sink asks of a Session before candles start arriving."""

    def warm_up(candles: tuple[Candle, ...], received_at: datetime) -> None:
        session.warm_up_completed_candles(candles, received_at=received_at)

    return warm_up


def read_open_basket_state(
    trade_history: SQLiteTradeHistory,
    session_id: UUID,
) -> OpenBasketState | None:
    """Read back the Basket a previous Runtime left open, if there is one.

    A Basket nobody holds is a Position nobody watches: its take profit goes
    unchecked and its Entries go uncounted. Rather than trade beside one, a
    Basket that cannot be put back says so.
    """
    basket = trade_history.find_open_basket(session_id)
    if basket is None:
        return None
    fills = trade_history.list_fills(basket.basket_id)
    # An Entry that was sold at its own target is not part of the position
    # left open, however the Basket it belonged to still is.
    sold = {
        fill.closes_entry_number
        for fill in fills
        if fill.closes_entry_number is not None
    }
    entries: list[OpenBasketEntry] = []
    for fill in fills:
        if fill.entry_number is None or fill.entry_number in sold:
            continue
        if fill.entry_atr is None:
            raise PaperPositionRestoreError(
                "open Basket has an Entry recorded without its ATR"
            )
        entries.append(
            OpenBasketEntry(
                entry_number=fill.entry_number,
                price=fill.price,
                quantity=fill.quantity,
                filled_at=fill.filled_at_utc,
                atr=fill.entry_atr,
            )
        )
    if not entries:
        raise PaperPositionRestoreError("open Basket has no Entry Fills")
    return OpenBasketState(
        basket_id=basket.basket_id,
        entries=tuple(entries),
        closed_basket_count=trade_history.count_closed_baskets(session_id),
    )


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def _workspace_matches_session(
    workspace: TradingWorkspaceSnapshot,
    session: ConfiguredPaperSession,
) -> bool:
    header = workspace.header
    if header is None:
        return False
    return (
        header.symbol == session.market_data.symbol
        and header.timeframe == session.market_data.timeframe
        and header.trade_mode is session.config.trade_mode
        and header.market_type is session.config.market_type
        and header.preset_version == session.config.preset_version
    )
