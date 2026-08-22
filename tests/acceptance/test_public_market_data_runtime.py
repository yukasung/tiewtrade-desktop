from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

import pytest

from tests.support.market_data_logging import market_data_records
from tiewtrade.application.paper_spot_market_data import PaperSpotMarketDataSink
from tiewtrade.application.paper_spot_session import PaperSpotSession
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataKlineUpdate
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.market_data.runtime import MarketDataRuntime
from tiewtrade.market_data.runtime_logging import MarketDataEventName
from tiewtrade.market_data.runtime_state import (
    MarketDataRuntimeReason,
    MarketDataRuntimeState,
)
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataFatalError,
    MarketDataRateLimitError,
)
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules

_NOW = datetime(2026, 1, 1, 1, 20, tzinfo=UTC)
_T = TypeVar("_T")


class FakePublicCandleSource:
    def __init__(
        self,
        *,
        warm_up: Iterable[Candle],
        live: Iterable[Candle],
    ) -> None:
        self._warm_up = tuple(warm_up)
        self._live = tuple(
            MarketDataKlineUpdate(candle=candle, is_closed=True) for candle in live
        )
        self._closed = asyncio.Event()
        self.requested_warm_up_count: int | None = None
        self.close_count = 0

    async def load_recent(
        self,
        config: MarketDataConfig,
        *,
        count: int,
        completed_before: datetime,
    ) -> tuple[Candle, ...]:
        self.requested_warm_up_count = count
        return self._warm_up

    async def load_range(
        self,
        config: MarketDataConfig,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        raise AssertionError("contiguous acceptance candles must not backfill")

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        return self._stream_klines()

    async def close(self) -> None:
        if self._closed.is_set():
            return
        self.close_count += 1
        self._closed.set()

    async def _stream_klines(self) -> AsyncIterator[MarketDataKlineUpdate]:
        for update in self._live:
            yield update
        await self._closed.wait()


class FakeRuntimeScheduler:
    def now(self) -> datetime:
        return _NOW

    async def sleep(self, seconds: float) -> None:
        raise AssertionError("contiguous acceptance flow must not reconnect")

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T:
        return await awaitable


class FakeFailureRuntimeScheduler(FakeRuntimeScheduler):
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        # Retrying has no end of its own any more, so a wait that never gives
        # the loop back would starve the test watching it.
        await asyncio.sleep(0)


class FailedKlineStream:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def __aiter__(self) -> FailedKlineStream:
        return self

    async def __anext__(self) -> MarketDataKlineUpdate:
        raise self._failure


class FakeFailingPublicCandleSource(FakePublicCandleSource):
    def __init__(
        self,
        *,
        warm_up: Iterable[Candle],
        failure: Exception,
    ) -> None:
        super().__init__(warm_up=warm_up, live=())
        self._failure = failure
        self.stream_count = 0

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        self.stream_count += 1
        return FailedKlineStream(self._failure)


def test_fake_public_runtime_warms_then_processes_paper_spot_live_candle() -> None:
    market_data = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    warm_up = candles(start=0, count=15, config=market_data)
    live = candles(start=75, count=1, config=market_data)
    source = FakePublicCandleSource(warm_up=warm_up, live=live)
    sink = PaperSpotMarketDataSink(configured_paper_spot_session(market_data))
    observed_states: list[MarketDataRuntimeState] = []
    runtime = MarketDataRuntime(
        config=market_data,
        warm_up_count=len(warm_up),
        source=source,
        sink=sink,
        scheduler=FakeRuntimeScheduler(),
        on_transition=lambda snapshot: observed_states.append(snapshot.state),
    )

    asyncio.run(run_until_sink_receives(runtime, sink, count=len(live)))

    assert observed_states.index(
        MarketDataRuntimeState.WARMING_UP
    ) < observed_states.index(MarketDataRuntimeState.LIVE)
    assert runtime.snapshot.state is MarketDataRuntimeState.STOPPED
    assert not hasattr(runtime, "visited_states")
    assert source.requested_warm_up_count == len(warm_up)
    assert source.close_count == 1
    assert sink.last_snapshot is not None
    assert sink.last_snapshot.accepted is True
    assert sink.live_candle_count == len(live)
    assert set(inspect.signature(FakePublicCandleSource).parameters) == {
        "warm_up",
        "live",
    }
    assert not hasattr(source, "credentials")
    assert not hasattr(source, "order_transport")
    assert not hasattr(source, "place_order")


def test_rate_limit_waits_for_the_provider_without_paper_spot_delivery() -> None:
    """A provider that keeps refusing is waited on, not given up on.

    Nothing is delivered while it refuses, which is the part that matters
    here: waiting is not the same as trading on data that never arrived.
    """
    runtime, source, sink, scheduler = runtime_with_failing_stream(
        MarketDataRateLimitError("429", retry_after=None)
    )

    async def exercise() -> None:
        run_task = asyncio.create_task(runtime.run())
        # Past the four attempts the old policy would have stopped at.
        for _ in range(1000):
            if source.stream_count > 5:
                break
            await asyncio.sleep(0)
        assert runtime.snapshot.state is not MarketDataRuntimeState.FAILED_CLOSED
        await runtime.stop()
        await run_task

    asyncio.run(exercise())

    assert runtime.snapshot.state is MarketDataRuntimeState.STOPPED
    assert scheduler.sleeps[:4] == [60.0, 60.0, 60.0, 60.0]
    assert sink.live_candle_count == 0
    assert sink.last_snapshot is None


def test_fatal_source_failure_fails_closed_without_paper_spot_delivery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("acceptance.market_data.fatal_source_failure")
    caplog.set_level(logging.INFO, logger=logger.name)
    runtime, source, sink, scheduler = runtime_with_failing_stream(
        MarketDataFatalError(
            "bad request",
            kind=MarketDataFailureKind.PROTOCOL,
        ),
        logger=logger,
    )

    asyncio.run(runtime.run())

    assert runtime.snapshot.state is MarketDataRuntimeState.FAILED_CLOSED
    assert runtime.snapshot.reason is MarketDataRuntimeReason.SOURCE_FATAL
    assert scheduler.sleeps == []
    assert source.stream_count == 1
    assert sink.live_candle_count == 0
    assert sink.last_snapshot is None
    terminal_record = next(
        record
        for record in market_data_records(caplog.records)
        if record.event_name == MarketDataEventName.RUNTIME_FAILED_CLOSED.value
    )
    assert terminal_record.event_name == "market_data.runtime.failed_closed"
    assert terminal_record.reason == "source_fatal"
    assert terminal_record.failure_kind == "protocol"
    assert (
        not {
            "api_key",
            "secret",
            "credentials",
            "payload",
            "exception",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }
        & terminal_record.__dict__.keys()
    )


def runtime_with_failing_stream(
    failure: Exception,
    *,
    logger: logging.Logger | None = None,
) -> tuple[
    MarketDataRuntime,
    FakeFailingPublicCandleSource,
    PaperSpotMarketDataSink,
    FakeFailureRuntimeScheduler,
]:
    market_data = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    warm_up = candles(start=0, count=15, config=market_data)
    source = FakeFailingPublicCandleSource(
        warm_up=warm_up,
        failure=failure,
    )
    sink = PaperSpotMarketDataSink(configured_paper_spot_session(market_data))
    scheduler = FakeFailureRuntimeScheduler()
    runtime = MarketDataRuntime(
        config=market_data,
        warm_up_count=len(warm_up),
        source=source,
        sink=sink,
        scheduler=scheduler,
        logger=logger,
    )
    return runtime, source, sink, scheduler


async def run_until_sink_receives(
    runtime: MarketDataRuntime,
    sink: PaperSpotMarketDataSink,
    *,
    count: int,
) -> None:
    run_task = asyncio.create_task(runtime.run())
    try:
        for _ in range(100):
            if sink.live_candle_count >= count:
                break
            if run_task.done():
                await run_task
            await asyncio.sleep(0)
        else:
            raise AssertionError("runtime did not deliver the live candle")
    finally:
        await runtime.stop()
        await run_task


def configured_paper_spot_session(
    market_data: MarketDataConfig,
) -> PaperSpotSession:
    preset = RsiStepGridPreset.v1()
    return PaperSpotSession(
        SessionConfig(
            session_id=UUID("00000000-0000-0000-0000-000000000099"),
            preset_version=preset.version,
            market_type=MarketType.SPOT,
            trade_mode=TradeMode.PAPER,
            capital_per_entry=Decimal("1000"),
            entry_policy=EntryPolicy(max_entries=4),
        ),
        market_data,
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        preset,
    )


def candles(
    *,
    start: int,
    count: int,
    config: MarketDataConfig,
) -> tuple[Candle, ...]:
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Candle(
            symbol=config.symbol,
            timeframe=config.timeframe,
            open_time=origin + timedelta(minutes=minute),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
        )
        for minute in range(start, start + count * 5, 5)
    )
