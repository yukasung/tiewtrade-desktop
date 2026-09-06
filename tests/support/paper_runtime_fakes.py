"""A market feed and a clock a Paper Runtime can be driven by in a test.

Neither reaches a network. The feed hands over the warm-up it was built with,
then the live candles, then waits — a stream that ends on its own would stop
the Runtime before the test has finished asking it anything, and it is the
test that decides when the Bot stops.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime
from threading import Event
from typing import TypeVar

from tests.support.paper_runtime_candles import live_candles, warm_up_candles
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataKlineUpdate
from tiewtrade.market_data.config import MarketDataConfig

RUNTIME_NOW = datetime(2026, 8, 2, 3, tzinfo=UTC)
_T = TypeVar("_T")


class ImmediateScheduler:
    """Every wait the Runtime asks for, granted at once."""

    def now(self) -> datetime:
        return RUNTIME_NOW

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T:
        return await awaitable


class PaperRuntimeCandleSource:
    """The warm-up and live candles a Runtime is fed, and nothing else.

    Built with the run that opens an Entry by default; a Runtime that should
    be given a warm-up and then left alone is built with no live candles.
    """

    def __init__(
        self,
        *,
        warm_up: tuple[Candle, ...] | None = None,
        live: tuple[Candle, ...] | None = None,
    ) -> None:
        self._warm_up = warm_up_candles() if warm_up is None else warm_up
        self._live = live_candles() if live is None else live
        self.closed = Event()
        self.load_recent_calls = 0

    async def load_recent(
        self,
        config: MarketDataConfig,
        *,
        count: int,
        completed_before: datetime,
    ) -> tuple[Candle, ...]:
        self.load_recent_calls += 1
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
        return self._stream()

    async def close(self) -> None:
        self.closed.set()

    async def _stream(self) -> AsyncIterator[MarketDataKlineUpdate]:
        for candle in self._live:
            yield MarketDataKlineUpdate(candle=candle, is_closed=True)
        # Nothing more is coming. The test is what stops the Runtime.
        await asyncio.Event().wait()
