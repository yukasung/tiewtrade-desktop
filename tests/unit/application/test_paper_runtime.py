from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from typing import NoReturn, TypeVar
from uuid import UUID

import pytest

import tiewtrade.application.paper_runtime as paper_runtime_module
from tests.support.paper_runtime_candles import (
    LIVE_FROM,
    candle,
    live_candles,
    warm_up_candles,
)
from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tiewtrade.application.open_basket import reopen_basket
from tiewtrade.application.paper_runtime import (
    PaperRuntimeController,
    read_open_basket_state,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.session_notice import (
    BotErrorNotice,
    EntryOpenedNotice,
    MarketFeedLostNotice,
    MarketFeedRestoredNotice,
    SessionNotice,
    SessionNotifier,
)
from tiewtrade.application.trade_history import TradeHistoryFilter
from tiewtrade.application.trading_workspace import (
    BotRuntimeState,
    DataFreshness,
    TradingWorkspaceSnapshot,
    WorkspaceReadState,
)
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleRecord,
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import (
    SQLiteTradeHistory,
)
from tiewtrade.integrations.sqlite.trade_history_errors import (
    TradeHistoryUnavailableError,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataKlineUpdate
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.market_data.runtime_state import (
    MarketDataRuntimeReason,
    MarketDataRuntimeSnapshot,
    MarketDataRuntimeState,
)
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataFatalError,
)
from tiewtrade.trading.basket import BasketCloseReason
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)

_NOW = datetime(2026, 8, 2, 3, tzinfo=UTC)
_STOPPED_AT = datetime(2026, 8, 2, 4, tzinfo=UTC)
_T = TypeVar("_T")


class ImmediateScheduler:
    def now(self) -> datetime:
        return _NOW

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T:
        return await awaitable


class ControllablePublicCandleSource:
    def __init__(
        self,
        *,
        warm_up: tuple[Candle, ...],
        live: tuple[Candle, ...],
    ) -> None:
        self._warm_up = warm_up
        self._live = tuple(
            MarketDataKlineUpdate(candle=candle, is_closed=True) for candle in live
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._release: asyncio.Event | None = None
        self.closed = Event()

    async def load_recent(
        self,
        config: MarketDataConfig,
        *,
        count: int,
        completed_before: datetime,
    ) -> tuple[Candle, ...]:
        assert count == len(self._warm_up)
        return self._warm_up

    async def load_range(
        self,
        config: MarketDataConfig,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        raise AssertionError("contiguous fake candles must not backfill")

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        return self._stream_klines()

    async def close(self) -> None:
        self.closed.set()

    def fail_stream(self) -> None:
        deadline = Event()
        while self._loop is None or self._release is None:
            if deadline.wait(0.001):
                raise AssertionError("unreachable")
        self._loop.call_soon_threadsafe(self._release.set)

    async def _stream_klines(self) -> AsyncIterator[MarketDataKlineUpdate]:
        self._loop = asyncio.get_running_loop()
        self._release = asyncio.Event()
        for update in self._live:
            yield update
        await self._release.wait()
        raise MarketDataFatalError(
            "fake stream finished",
            kind=MarketDataFailureKind.TRANSPORT,
        )


class FailingSpotTradeHistory(SQLiteTradeHistory):
    def record_paper_spot_entry_fill(
        self,
        fill: TradeFill,
        *,
        symbol: str,
        timeframe: str,
        strategy_preset_version: str,
    ) -> bool:
        raise TradeHistoryUnavailableError(
            "sqlite failed at /private/tmp with api_key=secret"
        )


class FailingSaleSpotTradeHistory(SQLiteTradeHistory):
    """Records the Entry that was bought, and never the sale that closes it."""

    def record_closed_basket(
        self,
        basket: object,
        fill: object,
    ) -> bool:
        raise TradeHistoryUnavailableError(
            "sqlite failed at /private/tmp with api_key=secret"
        )


class FailingRuntimeLifecycle(SQLitePaperRuntimeLifecycle):
    def mark_running(
        self,
        session_id: UUID,
        observed_at_utc: datetime,
    ) -> None:
        raise RuntimeError("sqlite failed at /private/tmp with api_key=secret")


class RecordingRuntimeLifecycle(SQLitePaperRuntimeLifecycle):
    def __init__(self, database: SQLiteDatabase) -> None:
        super().__init__(database)
        self.running_marks = 0

    def mark_running(
        self,
        session_id: UUID,
        observed_at_utc: datetime,
    ) -> None:
        self.running_marks += 1
        super().mark_running(session_id, observed_at_utc)


class FatalWarmUpSource(ControllablePublicCandleSource):
    async def load_recent(
        self,
        config: MarketDataConfig,
        *,
        count: int,
        completed_before: datetime,
    ) -> tuple[Candle, ...]:
        raise MarketDataFatalError(
            "warm-up failed with api_key=secret",
            kind=MarketDataFailureKind.PAYLOAD,
        )


class FailingStopLifecycle(SQLitePaperRuntimeLifecycle):
    def mark_stopped(
        self,
        session_id: UUID,
        observed_at_utc: datetime,
    ) -> None:
        raise RuntimeError("sqlite failed at /private/tmp with api_key=secret")


class FailingReadLifecycle(SQLitePaperRuntimeLifecycle):
    def read(self, session_id: UUID) -> PaperRuntimeLifecycleRecord | None:
        raise RuntimeError("sqlite failed at /private/tmp with api_key=secret")


class MismatchedRuntimeLifecycle(SQLitePaperRuntimeLifecycle):
    def read(self, session_id: UUID) -> PaperRuntimeLifecycleRecord | None:
        return PaperRuntimeLifecycleRecord(
            session_id=UUID("10000000-0000-0000-0000-000000000099"),
            state=PaperRuntimeLifecycleState.STOPPED,
            observed_at_utc=_STOPPED_AT,
        )


class BlockingCloseSource(ControllablePublicCandleSource):
    def __init__(
        self,
        *,
        warm_up: tuple[Candle, ...],
        live: tuple[Candle, ...],
    ) -> None:
        super().__init__(warm_up=warm_up, live=live)
        self.close_started = Event()
        self._close_loop: asyncio.AbstractEventLoop | None = None
        self._close_release: asyncio.Event | None = None

    async def close(self) -> None:
        self._close_loop = asyncio.get_running_loop()
        self._close_release = asyncio.Event()
        self.close_started.set()
        await self._close_release.wait()
        self.closed.set()

    def release_close(self) -> None:
        assert self.close_started.wait(1)
        assert self._close_loop is not None
        assert self._close_release is not None
        self._close_loop.call_soon_threadsafe(self._close_release.set)


class FailingCloseSource(ControllablePublicCandleSource):
    async def close(self) -> None:
        self.closed.set()
        raise RuntimeError("transport failed with api_key=secret")


class QueuedPublicCandleSource(ControllablePublicCandleSource):
    def __init__(self, *, warm_up: tuple[Candle, ...]) -> None:
        super().__init__(warm_up=warm_up, live=())
        self._queue_loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[MarketDataKlineUpdate] | None = None
        self.stream_ready = Event()
        self.delivery_completed = Event()

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        return self._queued_stream()

    async def close(self) -> None:
        self.closed.set()

    def emit(self, candle: Candle) -> None:
        self._emit(MarketDataKlineUpdate(candle=candle, is_closed=True))

    def emit_open(self, candle: Candle) -> None:
        self._emit(MarketDataKlineUpdate(candle=candle, is_closed=False))

    def _emit(self, update: MarketDataKlineUpdate) -> None:
        assert self.stream_ready.wait(1)
        assert self._queue_loop is not None
        assert self._queue is not None
        asyncio.run_coroutine_threadsafe(
            self._queue.put(update),
            self._queue_loop,
        ).result(timeout=1)

    async def _queued_stream(self) -> AsyncIterator[MarketDataKlineUpdate]:
        self._queue_loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self.stream_ready.set()
        while True:
            yield await self._queue.get()
            self.delivery_completed.set()


def test_in_progress_candle_is_published_without_changing_workspace(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = QueuedPublicCandleSource(warm_up=warm_up_candles())
    in_progress_candles: list[Candle] = []
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        in_progress_candle_callback=in_progress_candles.append,
    )
    controller.start(session)
    before = controller.current_workspace
    forming = candle(LIVE_FROM, close=Decimal("87.25"))

    source.emit_open(forming)

    assert source.delivery_completed.wait(1)
    assert in_progress_candles == [forming]
    assert controller.current_workspace == before
    controller.stop(session)


def test_a_futures_session_cannot_start_without_a_market_to_read(
    tmp_path: Path,
) -> None:
    """Binance TH lists no Futures market, so there is no feed to start on.

    The Bot says so at the door and stays stopped, rather than reporting
    RUNNING over a stream that was never opened.
    """
    session = configured_futures_session()
    database = _database_with_session(tmp_path, session)
    source = QueuedPublicCandleSource(warm_up=warm_up_candles())
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.blocked_reason == "Paper Bot could not be started"
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is not BotRuntimeState.RUNNING


def test_in_progress_candle_observer_failure_does_not_block_runtime(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = QueuedPublicCandleSource(warm_up=warm_up_candles())
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        in_progress_candle_callback=lambda _candle: _raise_callback_failure(),
    )
    controller.start(session)

    source.emit_open(candle(LIVE_FROM, close=Decimal("87.25")))

    assert source.delivery_completed.wait(1)
    assert controller.current_result.workspace.header is not None
    assert controller.current_result.workspace.header.runtime_state is (
        BotRuntimeState.RUNNING
    )
    controller.stop(session)


@pytest.mark.parametrize("market_type", [MarketType.SPOT])
def test_start_selects_concrete_paper_market_and_publishes_live_basket(
    tmp_path: Path,
    market_type: MarketType,
) -> None:
    session = _session(market_type)
    database = _database_with_session(tmp_path, session)
    source = _source()
    lifecycle = RecordingRuntimeLifecycle(database)
    selected: list[BinancePublicEndpoints] = []
    snapshots: list[TradingWorkspaceSnapshot] = []
    completed_candles: list[Candle] = []
    basket_ready = Event()

    def publish(snapshot: TradingWorkspaceSnapshot) -> None:
        snapshots.append(snapshot)
        if snapshot.basket is not None:
            basket_ready.set()

    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda endpoints: _select_source(
            endpoints, source=source, selected=selected
        ),
        scheduler=ImmediateScheduler(),
        snapshot_callback=publish,
        completed_candle_callback=completed_candles.append,
    )

    result = controller.start(session)

    assert result.blocked_reason is None
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.RUNNING
    assert result.workspace.header.data_freshness is DataFreshness.FRESH
    assert basket_ready.wait(2)
    current = controller.current_workspace
    assert current.read_state is WorkspaceReadState.READY
    assert current.basket is not None
    assert current.basket.market_type == market_type.value
    assert current.basket.entry_count == 1
    # The Entry the runtime filled reaches the tab unaveraged, marked on its own
    # price, so a second Entry cannot hide inside an average.
    entry = current.basket.entries[0]
    assert entry.entry_number == 1
    assert entry.quantity == current.basket.total_quantity
    assert entry.price == current.basket.average_entry_price
    assert entry.notional == entry.price * entry.quantity
    assert entry.unrealized_pnl == current.basket.unrealized_pnl
    assert current.basket.total_quantity > 0
    assert current.basket.current_price == live_candles()[-1].close
    assert current.data_as_of_utc == live_candles()[-1].close_time
    assert selected == [BinancePublicEndpoints.for_market_type(market_type)]
    marker = SQLitePaperRuntimeLifecycle(database).read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING
    assert lifecycle.running_marks == 1
    history = SQLiteTradeHistory(database).list_basket_history(
        _history_filter(market_type)
    )
    assert len(history.items) == 1
    assert history.items[0].status is BasketStatus.OPEN
    assert MarketDataRuntimeState.LIVE in controller.observed_runtime_states
    assert completed_candles == list(live_candles())

    source.fail_stream()
    assert source.closed.wait(2)


def test_persistence_failure_blocks_and_preserves_last_known_workspace(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = _source()
    blocked_ready = Event()

    def publish(snapshot: TradingWorkspaceSnapshot) -> None:
        if (
            snapshot.header is not None
            and snapshot.header.runtime_state is BotRuntimeState.BLOCKED
        ):
            blocked_ready.set()

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=FailingSpotTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=publish,
    )

    started = controller.start(session)

    assert started.workspace.header is not None
    assert started.workspace.header.runtime_state is BotRuntimeState.RUNNING
    assert started.blocked_reason is None
    assert blocked_ready.wait(2)
    current = controller.current_workspace
    assert current.header is not None
    assert current.header.runtime_state is BotRuntimeState.BLOCKED
    assert current.read_state is WorkspaceReadState.STALE
    assert current.basket is None
    assert (
        controller.current_result.blocked_reason
        == "Paper Bot stopped: the Session could not continue"
    )
    assert "api_key" not in (controller.current_result.blocked_reason or "")
    assert source.closed.wait(2)


def test_lifecycle_failure_blocks_and_closes_the_runtime_source(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = _source()
    controller = PaperRuntimeController(
        lifecycle=FailingRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot could not be started"
    assert source.closed.wait(1)


def test_composition_failure_returns_blocked_instead_of_hanging(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _raise_composition_failure(),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )
    completed = Event()
    outcomes = []

    def start() -> None:
        outcomes.append(controller.start(session))
        completed.set()

    Thread(target=start, daemon=True).start()

    assert completed.wait(1)
    assert outcomes[0].workspace.header is not None
    assert outcomes[0].workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert outcomes[0].blocked_reason == "Paper Bot could not be started"


def test_snapshot_callback_failure_returns_blocked_and_stops_runtime(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = _source()
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: _raise_callback_failure(),
    )

    result = controller.start(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot could not be started"
    assert source.closed.wait(1)


def test_fatal_warm_up_failure_returns_sanitized_blocked(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = FatalWarmUpSource(warm_up=(), live=())
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot could not be started"
    assert "api_key" not in (result.blocked_reason or "")
    assert source.closed.wait(1)


@pytest.mark.parametrize(
    "state",
    [
        MarketDataRuntimeState.STALE,
        MarketDataRuntimeState.RECONNECTING,
        MarketDataRuntimeState.BACKFILLING,
        MarketDataRuntimeState.RATE_LIMITED,
    ],
)
def test_transient_runtime_state_projects_stale_until_live(
    tmp_path: Path,
    state: MarketDataRuntimeState,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = _source()
    snapshots: list[TradingWorkspaceSnapshot] = []
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=snapshots.append,
    )
    started = controller.start(session)
    assert started.workspace.header is not None
    assert started.workspace.header.runtime_state is BotRuntimeState.RUNNING

    controller._on_runtime_transition(  # noqa: SLF001
        session,
        _runtime_snapshot(state),
    )

    stale = controller.current_workspace
    assert stale.header is not None
    assert stale.header.runtime_state is BotRuntimeState.RUNNING
    assert stale.header.data_freshness is DataFreshness.STALE
    assert stale.read_state is WorkspaceReadState.STALE

    controller._on_runtime_transition(  # noqa: SLF001
        session,
        _runtime_snapshot(MarketDataRuntimeState.LIVE),
    )

    fresh = controller.current_workspace
    assert fresh.header is not None
    assert fresh.header.runtime_state is BotRuntimeState.RUNNING
    assert fresh.header.data_freshness is DataFreshness.FRESH
    assert fresh.read_state is WorkspaceReadState.READY
    source.fail_stream()
    assert source.closed.wait(2)


def test_start_takes_back_the_basket_a_previous_runtime_left_open(
    tmp_path: Path,
) -> None:
    """A Position outlives the Runtime that opened it, and is picked up again."""
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    source = _source()
    basket_ready = Event()

    def publish(snapshot: TradingWorkspaceSnapshot) -> None:
        if snapshot.basket is not None:
            basket_ready.set()

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=publish,
    )

    result = controller.start(session)

    assert result.blocked_reason is None
    assert basket_ready.wait(2)
    current = controller.current_workspace
    assert current.basket is not None
    assert current.basket.entry_count == 1
    assert current.basket.average_entry_price == Decimal("100")
    # 100 + 3 ATR, the target the Basket was carrying when it was left.
    assert current.basket.take_profit_price == Decimal("106")
    controller.stop(session)


def test_a_basket_taken_back_is_published_before_any_live_candle(
    tmp_path: Path,
) -> None:
    """Holding a Position is true from the Start, not from the next candle.

    A Basket is published when a candle completes, so a Session that took one
    back used to say nothing about it for a whole timeframe — an hour on an
    hourly Session, with the Position tab reading as though nothing were open
    over a Position that plainly was. No live candle arrives here at all.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    source = ControllablePublicCandleSource(warm_up=warm_up_candles(), live=())

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.blocked_reason is None
    basket = controller.current_workspace.basket
    assert basket is not None
    assert basket.entry_count == 1
    assert basket.average_entry_price == Decimal("100")
    # 100 + 3 ATR, the target it was carrying when it was left.
    assert basket.take_profit_price == Decimal("106")
    # Marked on the last warm-up candle, the last completed price it has seen.
    assert basket.current_price == warm_up_candles()[-1].close
    controller.stop(session)


def test_a_basket_down_to_its_second_entry_is_still_published(
    tmp_path: Path,
) -> None:
    """Entry 1 sold at its target, so what is left is Entry 2 on its own.

    A number is not handed back when an Entry leaves, so this Basket counts
    one Entry and calls it 2. The workspace once demanded 1..entry_count and
    refused to describe it at all, taking the Position tab down with it.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    history = SQLiteTradeHistory(database)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    basket_id = UUID("00000000-0000-0000-0000-0000000000b1")
    history.record_paper_spot_entry_fill(
        TradeFill(
            fill_id="paper:left-open:fill-2",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="entry:left-open-2",
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=2,
            filled_at_utc=_NOW + timedelta(minutes=5),
            price=Decimal("90"),
            quantity=Decimal("1"),
            notional=Decimal("90"),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
            entry_atr=Decimal("2"),
        ),
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
        strategy_preset_version=session.config.preset_version,
    )
    basket = history.get_basket(basket_id)
    assert basket is not None
    history.record_entry_exit_fill(
        basket,
        TradeFill(
            fill_id="paper:left-open:sale-1",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="take-profit:1",
            exchange_trade_id=None,
            side=FillSide.SELL,
            entry_number=None,
            filled_at_utc=_NOW + timedelta(minutes=10),
            price=Decimal("106"),
            quantity=Decimal("1"),
            realized_pnl=Decimal("5.8"),
            notional=Decimal("106"),
            source=FillSource.PAPER_EXECUTOR,
            closes_entry_number=1,
        ),
    )
    source = ControllablePublicCandleSource(warm_up=warm_up_candles(), live=())

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=history,
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.blocked_reason is None
    published = controller.current_workspace.basket
    assert published is not None
    assert published.entry_count == 1
    assert [entry.entry_number for entry in published.entries] == [2]
    assert published.average_entry_price == Decimal("90")
    controller.stop(session)


def test_a_running_runtime_sells_the_entry_it_is_asked_for(
    tmp_path: Path,
) -> None:
    """The Runtime holds the Basket, so the Runtime is what sells out of it.

    Written past it instead, the sale would be a Basket sold twice: once in
    the database and once by the next candle to reach a target the Runtime
    still thinks it holds.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    history = SQLiteTradeHistory(database)
    source = ControllablePublicCandleSource(warm_up=warm_up_candles(), live=())
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=history,
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )
    controller.start(session)

    sold = controller.close_entry(session, 1, price=Decimal("103.456"))

    assert sold is True
    # The Basket it was holding is gone from the database, and the workspace
    # it publishes no longer shows a Position it does not have.
    assert history.find_open_basket(session.config.session_id) is None
    assert controller.current_workspace.basket is None
    fills = history.list_fills(UUID("00000000-0000-0000-0000-0000000000b1"))
    assert [(fill.side.value, str(fill.price)) for fill in fills] == [
        ("buy", "100"),
        # Floored to the tick this Session trades in.
        ("sell", "103.4"),
    ]
    controller.stop(session)


def test_a_sale_that_kills_the_session_blocks_the_bot_instead_of_looking_alive(
    tmp_path: Path,
) -> None:
    """The Bot must not read as running once the Session behind it is done.

    A sale the trader asks for goes through the Session, and a Session that
    cannot finish one is finished itself: every candle after it is answered
    unaccepted. The Runtime keeps reading the market either way, so unless
    the failure is published the screen shows a Bot that is trading.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    published: list[TradingWorkspaceSnapshot] = []
    source = ControllablePublicCandleSource(warm_up=warm_up_candles(), live=())
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=FailingSaleSpotTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=published.append,
    )
    started = controller.start(session)
    assert started.workspace.header is not None
    assert started.workspace.header.runtime_state is BotRuntimeState.RUNNING

    with pytest.raises(TradeHistoryUnavailableError):
        controller.close_entry(session, 1, price=Decimal("103"))

    current = controller.current_workspace
    assert current.header is not None
    assert current.header.runtime_state is BotRuntimeState.BLOCKED
    assert (
        controller.current_result.blocked_reason
        == "Paper Bot stopped: the Session could not continue"
    )
    assert "api_key" not in (controller.current_result.blocked_reason or "")
    assert published[-1].header is not None
    assert published[-1].header.runtime_state is BotRuntimeState.BLOCKED
    controller.stop(session)


def test_an_entry_the_running_basket_does_not_hold_is_not_sold(
    tmp_path: Path,
) -> None:
    """A row on screen redraws under the hand; a number in it can be stale."""
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    history = SQLiteTradeHistory(database)
    source = ControllablePublicCandleSource(warm_up=warm_up_candles(), live=())
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=history,
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )
    controller.start(session)

    assert controller.close_entry(session, 7, price=Decimal("103")) is False
    assert history.find_open_basket(session.config.session_id) is not None
    controller.stop(session)


def test_a_runtime_that_is_not_running_sells_nothing(tmp_path: Path) -> None:
    """Nothing is holding the Basket here, so this is not the one to ask."""
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _source(),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    assert controller.close_entry(session, 1, price=Decimal("103")) is False


def test_a_basket_that_cannot_be_taken_back_blocks_the_start(
    tmp_path: Path,
) -> None:
    """An Entry recorded before its ATR was kept cannot state its target."""
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=None)
    source = _source()

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Position could not be restored"
    assert controller.current_workspace.basket is None


def test_futures_says_so_rather_than_trade_beside_an_open_basket(
    tmp_path: Path,
) -> None:
    session = configured_futures_session()
    database = _database_with_session(tmp_path, session)
    SQLiteTradeHistory(database).record_open_basket(
        BasketResult(
            basket_id=UUID("00000000-0000-0000-0000-0000000000b2"),
            session_id=session.config.session_id,
            trade_mode=TradeMode.PAPER,
            market_type=MarketType.FUTURES,
            leverage=session.config.futures_policy.leverage
            if session.config.futures_policy is not None
            else None,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            strategy_preset_version=session.config.preset_version,
            opened_at_utc=_NOW,
            closed_at_utc=None,
            entry_count=1,
            invested_notional=Decimal("100"),
            realized_pnl=Decimal("-0.1"),
            status=BasketStatus.OPEN,
        ),
        TradeFill(
            fill_id="paper:futures-left-open:fill",
            basket_id=UUID("00000000-0000-0000-0000-0000000000b2"),
            session_id=session.config.session_id,
            order_id="entry:futures-left-open",
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=1,
            filled_at_utc=_NOW,
            price=Decimal("100"),
            quantity=Decimal("1"),
            notional=Decimal("100"),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
        ),
    )
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _source(),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    result = controller.start(session)

    assert result.blocked_reason == "Paper Position could not be restored"


def test_a_basket_taken_back_leaves_out_the_entry_that_already_sold(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    history = SQLiteTradeHistory(database)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    basket_id = UUID("00000000-0000-0000-0000-0000000000b1")
    history.record_paper_spot_entry_fill(
        TradeFill(
            fill_id="paper:left-open:fill-2",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="entry:left-open-2",
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=2,
            filled_at_utc=_NOW + timedelta(minutes=5),
            price=Decimal("90"),
            quantity=Decimal("1"),
            notional=Decimal("90"),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
            entry_atr=Decimal("2"),
        ),
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
        strategy_preset_version=session.config.preset_version,
    )
    basket = history.get_basket(basket_id)
    assert basket is not None
    history.record_entry_exit_fill(
        basket,
        TradeFill(
            fill_id="paper:left-open:sale-2",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="take-profit:2",
            exchange_trade_id=None,
            side=FillSide.SELL,
            entry_number=None,
            filled_at_utc=_NOW + timedelta(minutes=10),
            price=Decimal("96"),
            quantity=Decimal("1"),
            notional=Decimal("96"),
            realized_pnl=Decimal("5.8"),
            source=FillSource.PAPER_EXECUTOR,
            closes_entry_number=2,
        ),
    )

    state = read_open_basket_state(history, session.config.session_id)

    assert state is not None
    # Entry 2 sold, so only Entry 1 is still part of the position.
    assert [entry.entry_number for entry in state.entries] == [1]
    # What it left is still part of the Basket's result, so it travels with
    # the Entries rather than being lost with them.
    assert state.settled.count == 1
    assert state.settled.quantity == Decimal("1")
    assert state.settled.notional == Decimal("90")
    assert state.settled.realized_pnl == Decimal("5.8")


def test_a_basket_taken_back_closes_on_the_entries_that_already_sold_too(
    tmp_path: Path,
) -> None:
    """A restart must not lose what the Basket had already made.

    The Runtime replays the Entries still open — the ones that sold are not
    in the position to replay — so closing the rest wrote a result and a
    count that left them out: 20 on a Basket that had made 25.8, over two
    Entries reported as one.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    history = SQLiteTradeHistory(database)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    basket_id = UUID("00000000-0000-0000-0000-0000000000b1")
    basket = history.get_basket(basket_id)
    assert basket is not None
    history.record_entry_exit_fill(
        replace(basket, realized_pnl=Decimal("5.8")),
        TradeFill(
            fill_id="paper:left-open:sale-1",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="take-profit:1",
            exchange_trade_id=None,
            side=FillSide.SELL,
            entry_number=None,
            filled_at_utc=_NOW + timedelta(minutes=10),
            price=Decimal("105.8"),
            quantity=Decimal("1"),
            notional=Decimal("105.8"),
            realized_pnl=Decimal("5.8"),
            source=FillSource.PAPER_EXECUTOR,
            closes_entry_number=1,
        ),
    )
    history.record_paper_spot_entry_fill(
        TradeFill(
            fill_id="paper:left-open:fill-2",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="entry:left-open-2",
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=2,
            filled_at_utc=_NOW + timedelta(minutes=15),
            price=Decimal("100"),
            quantity=Decimal("1"),
            notional=Decimal("100"),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
            entry_atr=Decimal("2"),
        ),
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
        strategy_preset_version=session.config.preset_version,
    )

    state = read_open_basket_state(history, session.config.session_id)
    assert state is not None
    reopened = reopen_basket(
        state,
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        tick_size=Decimal("0.01"),
    )

    closed = reopened.basket.close(
        exit_price=Decimal("120"),
        closed_at=_NOW + timedelta(minutes=20),
        close_reason=BasketCloseReason.MANUAL,
    )

    # 5.8 from the Entry that sold before, 20 from the one taken back.
    assert closed.realized_pnl == Decimal("25.8")
    assert closed.entry_count == 2


def _record_open_spot_entry(
    database: SQLiteDatabase,
    session: ConfiguredPaperSession,
    *,
    entry_atr: Decimal | None,
) -> None:
    basket_id = UUID("00000000-0000-0000-0000-0000000000b1")
    SQLiteTradeHistory(database).record_paper_spot_entry_fill(
        TradeFill(
            fill_id="paper:left-open:fill",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id="entry:left-open",
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=1,
            filled_at_utc=_NOW,
            price=Decimal("100"),
            quantity=Decimal("1"),
            notional=Decimal("100"),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
            entry_atr=entry_atr,
        ),
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
        strategy_preset_version=session.config.preset_version,
    )


@pytest.mark.parametrize("market_type", [MarketType.SPOT])
def test_stop_closes_runtime_marks_stopped_and_preserves_basket(
    tmp_path: Path,
    market_type: MarketType,
) -> None:
    session = _session(market_type)
    database = _database_with_session(tmp_path, session)
    source = _source()
    basket_ready = Event()

    def publish(snapshot: TradingWorkspaceSnapshot) -> None:
        if snapshot.basket is not None:
            basket_ready.set()

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=publish,
        clock=lambda: _STOPPED_AT,
    )
    controller.start(session)
    assert basket_ready.wait(2)
    running = controller.current_workspace
    assert running.basket is not None

    stopped = controller.stop(session)

    assert stopped.workspace.header is not None
    assert stopped.workspace.header.runtime_state is BotRuntimeState.STOPPED
    assert stopped.workspace.header.data_freshness is DataFreshness.UNAVAILABLE
    assert stopped.workspace.basket == running.basket
    assert stopped.workspace.basket.take_profit_price == (
        running.basket.take_profit_price
    )
    assert source.closed.wait(1)
    marker = SQLitePaperRuntimeLifecycle(database).read(session.config.session_id)
    assert marker == PaperRuntimeLifecycleRecord(
        session_id=session.config.session_id,
        state=PaperRuntimeLifecycleState.STOPPED,
        observed_at_utc=_STOPPED_AT,
    )


def test_stop_timeout_persists_blocked_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paper_runtime_module, "_STOP_TIMEOUT_SECONDS", 0.01)
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = BlockingCloseSource(
        warm_up=warm_up_candles(),
        live=live_candles(),
    )
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )
    controller.start(session)

    result = controller.stop(session)

    # A stop that already timed out still has to walk the close path through
    # a background thread before it gets here — more hops than the file's
    # other waits, and the one this repo's CI has actually caught running
    # short on a busy runner.
    assert source.close_started.wait(2)
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot could not be stopped"
    marker = SQLitePaperRuntimeLifecycle(database).read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.BLOCKED
    source.release_close()
    assert source.closed.wait(1)


def test_stop_lock_timeout_keeps_gate_closed_and_ignores_later_candle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paper_runtime_module, "_STOP_TIMEOUT_SECONDS", 0.01)
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = QueuedPublicCandleSource(warm_up=warm_up_candles())
    snapshots: list[TradingWorkspaceSnapshot] = []
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=snapshots.append,
    )
    controller.start(session)
    assert source.stream_ready.wait(1)

    controller._entry_lock.acquire()  # noqa: SLF001
    try:
        result = controller.stop(session)
    finally:
        controller._entry_lock.release()  # noqa: SLF001

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    snapshot_count = len(snapshots)
    source.emit(live_candles()[0])
    assert source.delivery_completed.wait(1)
    assert len(snapshots) == snapshot_count
    assert controller.current_workspace.header is not None
    assert controller.current_workspace.header.runtime_state is BotRuntimeState.BLOCKED

    monkeypatch.setattr(paper_runtime_module, "_STOP_TIMEOUT_SECONDS", 1.0)
    stopped = controller.stop(session)
    assert stopped.workspace.header is not None
    assert stopped.workspace.header.runtime_state is BotRuntimeState.STOPPED
    assert source.closed.wait(1)


def test_stop_lifecycle_failure_blocks_after_runtime_is_closed(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = _source()
    basket_ready = Event()

    def publish(snapshot: TradingWorkspaceSnapshot) -> None:
        if snapshot.basket is not None:
            basket_ready.set()

    controller = PaperRuntimeController(
        lifecycle=FailingStopLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=publish,
    )
    controller.start(session)
    assert basket_ready.wait(2)
    running = controller.current_workspace

    result = controller.stop(session)

    assert source.closed.wait(1)
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.workspace.basket == running.basket
    assert result.blocked_reason == "Paper Bot could not be stopped"
    assert "api_key" not in (result.blocked_reason or "")


def test_stop_controlled_loop_error_returns_sanitized_blocked(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    source = FailingCloseSource(
        warm_up=warm_up_candles(),
        live=live_candles(),
    )
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )
    controller.start(session)

    result = controller.stop(session)

    assert source.closed.wait(1)
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot could not be stopped"
    assert "api_key" not in (result.blocked_reason or "")


def test_recover_interrupted_running_marker_finishes_stopped_without_market_data(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_running(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.recover(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.STOPPED
    marker = lifecycle.read(session.config.session_id)
    assert marker == PaperRuntimeLifecycleRecord(
        session_id=session.config.session_id,
        state=PaperRuntimeLifecycleState.STOPPED,
        observed_at_utc=_STOPPED_AT,
    )


def test_recover_rejects_owned_running_runtime_and_persists_blocked_marker(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    source = _source()
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )
    controller.start(session)

    result = controller.recover(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot recovery failed"
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.BLOCKED
    assert not source.closed.is_set()

    stopped = controller.stop(session)
    assert stopped.workspace.header is not None
    assert stopped.workspace.header.runtime_state is BotRuntimeState.STOPPED
    assert source.closed.wait(1)


def test_recover_running_marker_write_failure_remains_blocked(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    SQLitePaperRuntimeLifecycle(database).mark_running(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=FailingStopLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.recover(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot recovery failed"
    marker = SQLitePaperRuntimeLifecycle(database).read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING


def test_recover_clean_stopped_marker_returns_configured_without_market_data(
    tmp_path: Path,
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_stopped(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.recover(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.CONFIGURED
    assert result.workspace.header.data_freshness is DataFreshness.NOT_STARTED
    assert result.workspace.basket is None


@pytest.mark.parametrize(
    "lifecycle_factory",
    [
        lambda database: SQLitePaperRuntimeLifecycle(database),
        FailingReadLifecycle,
        MismatchedRuntimeLifecycle,
    ],
)
def test_recover_missing_mismatched_or_unavailable_marker_blocks_safely(
    tmp_path: Path,
    lifecycle_factory: Callable[[SQLiteDatabase], SQLitePaperRuntimeLifecycle],
) -> None:
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    controller = PaperRuntimeController(
        lifecycle=lifecycle_factory(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.recover(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot recovery failed"
    assert "api_key" not in (result.blocked_reason or "")


def _database_with_session(
    tmp_path: Path,
    session: ConfiguredPaperSession,
) -> SQLiteDatabase:
    database = SQLiteDatabase(tmp_path / f"{session.config.market_type.value}.sqlite3")
    database.migrate()
    assert SQLiteActivePaperSessions(database).create(session).created
    return database


def _select_source(
    endpoints: BinancePublicEndpoints,
    *,
    source: ControllablePublicCandleSource,
    selected: list[BinancePublicEndpoints],
) -> ControllablePublicCandleSource:
    selected.append(endpoints)
    return source


def _raise_composition_failure() -> NoReturn:
    raise RuntimeError("transport payload with api_key=secret")


def _raise_callback_failure() -> None:
    raise RuntimeError("UI callback failed with api_key=secret")


def _unexpected_source_factory() -> NoReturn:
    raise AssertionError("recovery must not start market data")


def _source() -> ControllablePublicCandleSource:
    return ControllablePublicCandleSource(
        warm_up=warm_up_candles(),
        live=live_candles(),
    )


def _session(market_type: MarketType) -> ConfiguredPaperSession:
    if market_type is MarketType.SPOT:
        return configured_spot_session()
    return configured_futures_session()


def _symbol_rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


def _runtime_snapshot(state: MarketDataRuntimeState) -> MarketDataRuntimeSnapshot:
    reason = (
        MarketDataRuntimeReason.WARM_UP_COMPLETED
        if state is MarketDataRuntimeState.LIVE
        else MarketDataRuntimeReason.DATA_STALE
    )
    return MarketDataRuntimeSnapshot(
        state=state,
        reason=reason,
        transitioned_at=_NOW,
        last_accepted_open_time=live_candles()[-1].open_time,
    )


def _history_filter(market_type: MarketType) -> TradeHistoryFilter:
    return TradeHistoryFilter(market_type=market_type)


def test_a_filled_entry_is_said_out_loud_to_whoever_is_listening(
    tmp_path: Path,
) -> None:
    session = _session(MarketType.SPOT)
    database = _database_with_session(tmp_path, session)
    source = _source()
    notices: list[SessionNotice] = []
    notice_ready = Event()

    def notify(notice: SessionNotice) -> None:
        notices.append(notice)
        notice_ready.set()

    @asynccontextmanager
    async def notifier() -> AsyncIterator[SessionNotifier]:
        yield notify

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        notifier_factory=notifier,
    )

    controller.start(session)

    assert notice_ready.wait(2)
    controller.stop(session)
    notice = notices[0]
    assert isinstance(notice, EntryOpenedNotice)
    assert notice.market.symbol == session.market_data.symbol
    assert notice.market.timeframe == session.market_data.timeframe
    assert notice.entry_number == 1
    assert notice.market.session_id == session.config.session_id


def test_a_notifier_that_cannot_be_opened_does_not_keep_the_bot_from_trading(
    tmp_path: Path,
) -> None:
    """Settings that are wrong cost the message, never the Session.

    A token that has been withdrawn is found out here, on the way up, at the
    moment the trader has just asked the Bot to start. Refusing to start on
    it would make a chat notification a precondition of trading, which it is
    not — the Position tab remains the thing that says what is open.
    """
    session = _session(MarketType.SPOT)
    database = _database_with_session(tmp_path, session)
    source = _source()

    @asynccontextmanager
    async def notifier() -> AsyncIterator[SessionNotifier]:
        raise RuntimeError("the notifier could not be opened")
        yield  # pragma: no cover - never reached

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        notifier_factory=notifier,
    )

    result = controller.start(session)

    assert result.blocked_reason is None
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.RUNNING
    controller.stop(session)


def test_a_feed_that_goes_away_and_comes_back_is_said_once_each_way(
    tmp_path: Path,
) -> None:
    """One message when the candles stop, one when they start again.

    A feed in trouble transitions repeatedly on its way back — stale,
    reconnecting, stale again — and none of those are separate news to
    somebody reading a phone.
    """
    session = _session(MarketType.SPOT)
    database = _database_with_session(tmp_path, session)
    source = _source()
    notices: list[SessionNotice] = []

    @asynccontextmanager
    async def notifier() -> AsyncIterator[SessionNotifier]:
        yield notices.append

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        notifier_factory=notifier,
    )
    controller.start(session)
    notices.clear()

    for state in (
        MarketDataRuntimeState.STALE,
        MarketDataRuntimeState.RECONNECTING,
        MarketDataRuntimeState.STALE,
    ):
        controller._on_runtime_transition(session, _runtime_snapshot(state))  # noqa: SLF001
    controller._on_runtime_transition(  # noqa: SLF001
        session,
        _runtime_snapshot(MarketDataRuntimeState.LIVE),
    )

    assert [type(notice) for notice in notices] == [
        MarketFeedLostNotice,
        MarketFeedRestoredNotice,
    ]
    lost = notices[0]
    assert isinstance(lost, MarketFeedLostNotice)
    assert lost.market.symbol == session.market_data.symbol
    assert lost.reason == MarketDataRuntimeReason.DATA_STALE.value
    controller.stop(session)


def test_a_bot_that_went_live_without_trouble_says_nothing_about_the_feed(
    tmp_path: Path,
) -> None:
    """Nobody needs telling that what they just started has started."""
    session = _session(MarketType.SPOT)
    database = _database_with_session(tmp_path, session)
    source = _source()
    notices: list[SessionNotice] = []

    @asynccontextmanager
    async def notifier() -> AsyncIterator[SessionNotifier]:
        yield notices.append

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        notifier_factory=notifier,
    )
    controller.start(session)

    assert [
        notice for notice in notices if isinstance(notice, MarketFeedRestoredNotice)
    ] == []
    controller.stop(session)


def test_a_bot_that_stopped_says_so_once_and_says_why(tmp_path: Path) -> None:
    """One stop is one message, however many places notice it.

    Blocked is published by whoever finds out first and then by everyone else
    who was waiting on the same Session, and a trader wants to be told that
    the Bot stopped — not how many parts of it found out.
    """
    session = _session(MarketType.SPOT)
    database = _database_with_session(tmp_path, session)
    source = _source()
    notices: list[SessionNotice] = []

    @asynccontextmanager
    async def notifier() -> AsyncIterator[SessionNotifier]:
        yield notices.append

    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        notifier_factory=notifier,
    )
    controller.start(session)
    notices.clear()

    stopped = "Paper Bot stopped: the Session could not continue"
    controller._publish_blocked(reason=stopped)  # noqa: SLF001
    controller._publish_blocked(reason=stopped)  # noqa: SLF001

    errors = [notice for notice in notices if isinstance(notice, BotErrorNotice)]
    assert len(errors) == 1
    assert errors[0].reason == stopped
    assert errors[0].market.session_id == session.config.session_id
    controller.stop(session)


def test_a_controller_that_only_inspected_startup_is_not_holding_the_session(
    tmp_path: Path,
) -> None:
    """Reading durable lifecycle state is not taking a Basket in hand.

    A controller is put in place before any Bot runs so the tab can say what
    the last one left behind. Nothing is driving candles yet, so a sale asked
    for now belongs to the database, not to this.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _source(),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    controller.inspect_startup(session)

    assert controller.is_holding(session) is False


def test_a_running_controller_holds_its_session_until_it_is_stopped(
    tmp_path: Path,
) -> None:
    """What a Runtime holds it must be asked for, and only while it holds it.

    A stopped Bot leaves its Basket in the database, so the same question has
    to answer the other way round the moment it stops — otherwise a sale is
    asked of a Runtime that will never write it.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _source(),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    controller.start(session)

    assert controller.is_holding(session) is True

    controller.stop(session)

    assert controller.is_holding(session) is False


def test_startup_recovers_a_stale_running_marker_when_no_other_process_runs(
    tmp_path: Path,
) -> None:
    """A marker left saying "running" is a fact about a process that is gone.

    Nothing else can say so: the marker is written before the Bot runs and
    cleared after it stops, so a Bot that was killed leaves it standing. Told
    that this process is the only one on the installation, the marker is
    read for what it is — left over — and the Session is startable again.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_running(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.inspect_startup(session, sole_process=True)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.STOPPED
    assert result.blocked_reason is None
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.STOPPED


def test_startup_recovery_does_not_start_the_bot(tmp_path: Path) -> None:
    """Clearing a stale marker is not a command to trade.

    Start is the trader's press, and a Session that came back on its own
    would be one nobody asked for. `_unexpected_source_factory` proves no
    market data was opened; holding nothing proves no Basket was taken up.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_running(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    controller.inspect_startup(session, sole_process=True)

    assert controller.is_holding(session) is False


def test_startup_keeps_blocking_a_running_marker_when_nothing_was_proved(
    tmp_path: Path,
) -> None:
    """Not knowing is not the same as knowing there is no one there.

    Where the installation could not be claimed — the lock unavailable, or
    another window genuinely holding it — the marker may well be live, and a
    second Bot on one Session is the thing this exists to prevent.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_running(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.inspect_startup(session)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert result.blocked_reason == "Paper Bot recovery required"
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING


def test_startup_leaves_a_stopped_marker_alone_whoever_holds_the_installation(
    tmp_path: Path,
) -> None:
    """A Session that stopped cleanly reads the same either way."""
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_stopped(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.inspect_startup(session, sole_process=True)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.CONFIGURED
    assert result.blocked_reason is None


def test_startup_recovery_that_cannot_be_written_stays_blocked(
    tmp_path: Path,
) -> None:
    """Fail closed: an unwritten recovery must never read as a recovered one."""
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    SQLitePaperRuntimeLifecycle(database).mark_running(session.config.session_id, _NOW)
    controller = PaperRuntimeController(
        lifecycle=FailingStopLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    result = controller.inspect_startup(session, sole_process=True)

    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    marker = SQLitePaperRuntimeLifecycle(database).read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING


def test_a_killed_bot_comes_back_to_the_position_it_left(tmp_path: Path) -> None:
    """The whole point, end to end: a crash costs a restart, not a Position.

    A Bot killed mid-Session leaves an open Entry in the database and a
    marker still saying "running". Starting again is what the trader wants
    and what the Entry needs — the Basket is picked up where it was left,
    not sold, not abandoned, and not walled off behind a Blocked Session.
    """
    session = configured_spot_session()
    database = _database_with_session(tmp_path, session)
    _record_open_spot_entry(database, session, entry_atr=Decimal("2"))
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    lifecycle.mark_running(session.config.session_id, _NOW)
    history = SQLiteTradeHistory(database)
    left_open = history.find_open_basket(session.config.session_id)
    assert left_open is not None

    startup = PaperRuntimeController(
        lifecycle=lifecycle,
        trade_history=history,
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: _unexpected_source_factory(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: _STOPPED_AT,
    )

    recovered = startup.inspect_startup(session, sole_process=True)

    assert recovered.workspace.header is not None
    assert recovered.workspace.header.runtime_state is BotRuntimeState.STOPPED
    assert history.find_open_basket(session.config.session_id) is not None

    running = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=history,
        symbol_rules=_symbol_rules(),
        source_factory=lambda _endpoints: ControllablePublicCandleSource(
            warm_up=warm_up_candles(),
            live=(),
        ),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
    )

    started = running.start(session)

    assert started.workspace.header is not None
    assert started.workspace.header.runtime_state is BotRuntimeState.RUNNING
    basket = running.current_workspace.position_basket.basket
    assert basket is not None
    assert basket.entry_count == left_open.entry_count
    assert [entry.entry_number for entry in basket.entries] == [1]
    assert basket.total_quantity == Decimal("1")

    running.stop(session)
