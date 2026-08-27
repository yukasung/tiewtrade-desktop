from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

from tiewtrade.application.bot_control import (
    BotLifecycleResult,
    workspace_with_runtime_state,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_session_sink import (
    PaperPositionRestoreError as PaperPositionRestoreError,
)
from tiewtrade.application.paper_session_sink import (
    PaperRuntimeSink,
    paper_session_sink,
    session_market,
)
from tiewtrade.application.paper_session_sink import (
    read_open_basket_state as read_open_basket_state,
)
from tiewtrade.application.session_notice import (
    BotErrorNotice,
    MarketFeedLostNotice,
    MarketFeedRestoredNotice,
    SessionNotice,
    SessionNotifier,
)
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
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
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
    MarketDataRuntimeReason,
    MarketDataRuntimeSnapshot,
    MarketDataRuntimeState,
)
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.session_config import TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules

_logger = logging.getLogger(__name__)

_START_BLOCKED_REASON = "Paper Bot could not be started"
_STOP_BLOCKED_REASON = "Paper Bot could not be stopped"
_RECOVERY_BLOCKED_REASON = "Paper Bot recovery failed"
_RECOVERY_REQUIRED_REASON = "Paper Bot recovery required"
_RESTORE_BLOCKED_REASON = "Paper Position could not be restored"
_RUNTIME_DATA_BLOCKED_REASON = "Paper Bot stopped: market data could not be recovered"
# One message for every way the Session itself ends: a write that could not
# be confirmed, execution that broke an invariant, and the candle refused
# afterwards because of either. Naming only one of them would be a guess.
_SESSION_BLOCKED_REASON = "Paper Bot stopped: the Session could not continue"
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
        notifier_factory: (
            Callable[[], AbstractAsyncContextManager[SessionNotifier]] | None
        ) = None,
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
        self._notifier_factory = notifier_factory
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
        self._sink: PaperRuntimeSink[Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None
        self._active_session: ConfiguredPaperSession | None = None
        self._observed_runtime_states: list[MarketDataRuntimeState] = []
        self._notify: SessionNotifier | None = None
        # Whether the feed is currently believed to be delivering. A market
        # that keeps dropping and coming back is one message each way, not one
        # per transition it makes on its way there.
        self._feed_delivering = False
        # Blocked is published from more than one place, and more than once
        # for the same stop. The Bot only stops once, so it only says so once.
        self._error_said = False

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
        async with AsyncExitStack() as stack:
            await self._run_session(session, await self._open_notifier(stack))

    async def _open_notifier(self, stack: AsyncExitStack) -> SessionNotifier | None:
        """Open whatever says what the Bot did, or run without one.

        Settings that are wrong — a token that will not open a connection —
        are a reason for the Bot to say nothing, never a reason for it not to
        trade. The Session is what the trader started; the message is not.
        """
        if self._notifier_factory is None:
            return None
        try:
            return await stack.enter_async_context(self._notifier_factory())
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("the notifier could not be opened")
            return None

    async def _run_session(
        self,
        session: ConfiguredPaperSession,
        notify: SessionNotifier | None,
    ) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._notify = notify
            self._feed_delivering = False
            self._error_said = False
        terminal = asyncio.Event()

        def on_transition(snapshot: MarketDataRuntimeSnapshot) -> None:
            self._on_runtime_transition(session, snapshot)
            if snapshot.state in {
                MarketDataRuntimeState.STOPPED,
                MarketDataRuntimeState.FAILED_CLOSED,
            }:
                terminal.set()

        preset = strategy_definition_for(session.config.preset_version).build_preset()
        sink = paper_session_sink(
            session,
            preset,
            trade_history=self._trade_history,
            symbol_rules=self._symbol_rules,
            publish=self._publish_candle,
            entry_lock=self._entry_lock,
            entries_enabled=self._entries_enabled.is_set,
            restored=self._publish_restored_basket,
            notify=notify,
            clock=self._clock,
        )
        with self._lock:
            self._sink = sink
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

    def _on_runtime_transition(
        self,
        session: ConfiguredPaperSession,
        snapshot: MarketDataRuntimeSnapshot,
    ) -> None:
        with self._lock:
            self._observed_runtime_states.append(snapshot.state)
        if snapshot.state is MarketDataRuntimeState.LIVE:
            self._say_the_feed_came_back(session, snapshot)
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
            # A Bot that ran and then stopped is not a Bot that failed to
            # start, and the screen says whichever of the two this was. A
            # Session that never went live has not run yet, whatever ended it.
            with self._lock:
                had_run = self._readiness_result is not None
            self._publish_blocked(
                readiness=True,
                reason=(
                    _failed_closed_reason(snapshot.reason)
                    if had_run
                    else _START_BLOCKED_REASON
                ),
            )
        elif snapshot.state in {
            MarketDataRuntimeState.STALE,
            MarketDataRuntimeState.RECONNECTING,
            MarketDataRuntimeState.BACKFILLING,
            MarketDataRuntimeState.RATE_LIMITED,
        }:
            self._say_the_feed_went_away(session, snapshot)
            with self._lock:
                ready = self._readiness_result is not None
            if ready:
                self._publish_or_block(
                    BotLifecycleResult(
                        workspace=stale_workspace_snapshot(self.current_workspace)
                    )
                )

    def _say_the_feed_went_away(
        self,
        session: ConfiguredPaperSession,
        snapshot: MarketDataRuntimeSnapshot,
    ) -> None:
        """Say once that candles stopped arriving, however long they stay away.

        A feed in trouble transitions repeatedly — stale, reconnecting, rate
        limited, stale again — and every one of those is the same fact to
        somebody reading a phone. Only the first is said, and nothing more is
        until the feed has come back to go away again.
        """
        with self._lock:
            if not self._feed_delivering:
                return
            self._feed_delivering = False
        self._say(
            MarketFeedLostNotice(
                market=session_market(session),
                reason=snapshot.reason.value,
                observed_at=snapshot.transitioned_at,
            )
        )

    def _say_the_feed_came_back(
        self,
        session: ConfiguredPaperSession,
        snapshot: MarketDataRuntimeSnapshot,
    ) -> None:
        """Say that candles are arriving again, having said that they stopped.

        The first time a Session goes live is a Bot starting rather than a
        Bot recovering, and nobody needs telling that what they just started
        has started.
        """
        with self._lock:
            said_it_went = not self._feed_delivering and self._readiness_result
            self._feed_delivering = True
        if not said_it_went:
            return
        self._say(
            MarketFeedRestoredNotice(
                market=session_market(session),
                observed_at=snapshot.transitioned_at,
            )
        )

    def _say_the_bot_stopped(self, reason: str) -> None:
        """Say that the Bot has gone Blocked, once per stop.

        Blocked is published from wherever it is discovered, and one stop can
        be discovered more than once — by the candle that failed, by the ask
        the trader had in flight, and by the Runtime noticing afterwards. The
        trader is told about the stop, not about each of its discoveries.
        """
        with self._lock:
            session = self._active_session
            if self._error_said or session is None:
                return
            self._error_said = True
        self._say(
            BotErrorNotice(
                market=session_market(session),
                reason=reason,
                observed_at=self._clock(),
            )
        )

    def _say(self, notice: SessionNotice) -> None:
        """Hand one notice to whoever is listening, and never mind if nobody is.

        Notices are raised from the Runtime thread and from whichever thread a
        trader asked on. What that costs is the notifier's to answer for; what
        it may never cost is the Session, so nothing thrown here gets out.
        """
        with self._lock:
            notify = self._notify
        if notify is None:
            return
        try:
            notify(notice)
        except Exception:
            _logger.exception("saying what the Bot did failed")

    def close_entry(
        self,
        session: ConfiguredPaperSession,
        entry_number: int,
        *,
        price: Decimal,
    ) -> bool:
        """Sell one Entry of the running Session's Basket, at a price now.

        The Runtime holds the Basket while it runs, so a sale written behind
        its back would be a Basket sold twice — once in the database and once
        by the next candle to reach the target it still thinks it holds. The
        ask goes to the Session that owns it instead, under the lock a candle
        takes, and what is left is published the way a Fill is.
        """
        self._validate_session(session)
        with self._lock:
            sink = self._sink
            active = self._active_session
        if sink is None or active != session:
            return False
        with self._blocked_on_failure():
            return sink.close_entry(
                entry_number, price=price, observed_at=self._clock()
            )

    def waive_cooldown(self, session: ConfiguredPaperSession) -> bool:
        """Lift the Cooldown holding the running Session's Basket now.

        The Runtime holds the Basket's lifecycle while it runs, so the ask
        goes to the Session that owns it, under the same lock a candle takes.
        """
        self._validate_session(session)
        with self._lock:
            sink = self._sink
            active = self._active_session
        if sink is None or active != session:
            return False
        with self._blocked_on_failure():
            return sink.waive_cooldown(self._clock())

    @contextmanager
    def _blocked_on_failure(self) -> Iterator[None]:
        """Publish Blocked when an ask from the trader ends the Session.

        Everything reachable through here is terminal where it fails: a
        Session that failed closed answers nothing but unaccepted snapshots,
        and persistence that could not confirm a write refuses the next
        candle for good. The Runtime is still reading the market either way,
        so without this the screen would keep saying the Bot is trading.

        The Runtime is left to notice on its next candle, which the sink
        refuses on a Session that has stopped — reaching into its loop from
        the thread the trader is waiting on would be a deadlock looking for
        a reason. The error carries on to whoever asked.
        """
        try:
            yield
        except Exception:
            self._publish_blocked(reason=_SESSION_BLOCKED_REASON)
            raise

    def _publish_restored_basket(
        self,
        basket: BasketSnapshot,
        observed_at_utc: datetime,
    ) -> None:
        """Put the Basket that was taken back into the workspace at once.

        Only the workspace: the candle it is marked on is a warm-up candle the
        chart already has, and handing it round again as a completed candle
        would be reporting it twice.
        """
        self._publish(
            BotLifecycleResult(
                workspace=paper_runtime_workspace_snapshot(
                    self.current_workspace,
                    basket=basket,
                    observed_at_utc=observed_at_utc,
                )
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
        self._say_the_bot_stopped(reason)
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


def _failed_closed_reason(reason: MarketDataRuntimeReason) -> str:
    """Say which half of the run stopped: reading the market, or saving it."""
    if reason is MarketDataRuntimeReason.SINK_ERROR:
        return _SESSION_BLOCKED_REASON
    return _RUNTIME_DATA_BLOCKED_REASON


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
