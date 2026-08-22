from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import CancelledError
from datetime import datetime
from threading import Lock, Thread, current_thread
from typing import Protocol
from uuid import UUID

from tiewtrade.application.chart_data import ChartFeedState
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.latest_market_price import LatestMarketPrice
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_pipeline import MarketDataCandleSink
from tiewtrade.market_data.candle_source import MarketDataCandleSource
from tiewtrade.market_data.runtime import MarketDataRuntime
from tiewtrade.market_data.runtime_state import (
    MarketDataRuntimeSnapshot,
    MarketDataRuntimeState,
)

_LOGGER = logging.getLogger("tiewtrade.application.chart_realtime_feed")

_FEED_STATES = {
    MarketDataRuntimeState.LIVE: ChartFeedState.LIVE,
    MarketDataRuntimeState.STALE: ChartFeedState.RECONNECTING,
    MarketDataRuntimeState.RECONNECTING: ChartFeedState.RECONNECTING,
    MarketDataRuntimeState.BACKFILLING: ChartFeedState.RECONNECTING,
    MarketDataRuntimeState.RATE_LIMITED: ChartFeedState.RECONNECTING,
    MarketDataRuntimeState.FAILED_CLOSED: ChartFeedState.STOPPED,
    MarketDataRuntimeState.STOPPED: ChartFeedState.STOPPED,
}


class _Runtime(Protocol):
    async def run(self) -> None: ...

    async def stop(self) -> None: ...


RuntimeFactory = Callable[..., _Runtime]


class _ChartOnlySink(MarketDataCandleSink):
    def __init__(self, publish_completed: Callable[[Candle], None]) -> None:
        self._publish_completed = publish_completed

    async def warm_up(
        self,
        candles: tuple[Candle, ...],
        *,
        received_at: datetime,
    ) -> None:
        del candles, received_at

    async def process_completed(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool,
    ) -> None:
        # A chart draws a Candle wherever it came from; only trading cares.
        del received_at, backfilled
        self._publish_completed(candle)

    async def process_in_progress(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> None:
        """A chart-only feed never trades, so a forming candle is nothing to act on.

        Its own live candle reaches the chart through the runtime's open-candle
        callback, not through this sink.
        """
        del candle, received_at


class ChartOnlyFeedController:
    """Owns public candle facts for the chart without trading side effects."""

    def __init__(
        self,
        *,
        source_factory: Callable[[BinancePublicEndpoints], MarketDataCandleSource],
        runtime_factory: RuntimeFactory = MarketDataRuntime,
        latest_market_price: LatestMarketPrice | None = None,
    ) -> None:
        self._source_factory = source_factory
        self._runtime_factory = runtime_factory
        self._latest_market_price = latest_market_price
        self._lock = Lock()
        self._runtime: _Runtime | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None

    def start(
        self,
        session: ConfiguredPaperSession,
        publish_completed: Callable[[Candle], None],
        publish_in_progress: Callable[[Candle], None],
        selection: ChartViewSelection | None = None,
        *,
        publish_state: Callable[[ChartFeedState], None],
    ) -> None:
        self.stop()
        if self._latest_market_price is not None:
            self._latest_market_price.reset()
        captures_session_price = (
            selection is None or selection.market_data_config() == session.market_data
        )
        publish_completed_with_price = self._capture_price(
            session.config.session_id,
            publish_completed,
            capture_price=captures_session_price,
        )
        publish_in_progress_with_price = self._capture_price(
            session.config.session_id,
            publish_in_progress,
            capture_price=captures_session_price,
        )
        ready = Thread(
            target=self._run,
            args=(
                session,
                selection,
                publish_completed_with_price,
                publish_in_progress_with_price,
                publish_state,
            ),
            daemon=True,
            name=f"chart-feed-{session.config.session_id}",
        )
        with self._lock:
            self._thread = ready
        ready.start()

    def _capture_price(
        self,
        session_id: UUID,
        publish: Callable[[Candle], None],
        *,
        capture_price: bool,
    ) -> Callable[[Candle], None]:
        def capture(candle: Candle) -> None:
            latest_market_price = self._latest_market_price
            if capture_price and latest_market_price is not None:
                latest_market_price.observe(session_id, candle.close)
            publish(candle)

        return capture

    def stop(self) -> None:
        with self._lock:
            runtime, loop, thread = self._runtime, self._loop, self._thread
            self._runtime = None
            self._loop = None
            self._thread = None
        if runtime is not None and loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(runtime.stop(), loop).result(
                    timeout=30
                )
            except CancelledError:
                # The runtime's own run() already exited (e.g. it failed closed)
                # and its event loop cancelled our stop() request during
                # shutdown. The runtime is already stopped either way.
                pass
        if thread is not None and thread is not current_thread():
            thread.join(timeout=30)

    def close(self) -> None:
        self.stop()

    def _run(
        self,
        session: ConfiguredPaperSession,
        selection: ChartViewSelection | None,
        publish_completed: Callable[[Candle], None],
        publish_in_progress: Callable[[Candle], None],
        publish_state: Callable[[ChartFeedState], None],
    ) -> None:
        try:
            asyncio.run(
                self._run_async(
                    session,
                    selection,
                    publish_completed,
                    publish_in_progress,
                    publish_state,
                )
            )
        except Exception as error:
            # The thread ends here either way; what it must not do is end
            # without saying so. The type alone names the fault — a message
            # from a transport can carry a URL, and this is written to a file.
            _LOGGER.error(
                "chart.feed.crashed",
                extra={
                    "event_name": "chart.feed.crashed",
                    "failure": type(error).__name__,
                },
            )
        finally:
            # Stopped on purpose or given up on, a chart left without a feed
            # can say so. Unsaid, it goes on showing the last prices it has.
            _publish_safely(publish_state, ChartFeedState.STOPPED)

    async def _run_async(
        self,
        session: ConfiguredPaperSession,
        selection: ChartViewSelection | None,
        publish_completed: Callable[[Candle], None],
        publish_in_progress: Callable[[Candle], None],
        publish_state: Callable[[ChartFeedState], None],
    ) -> None:
        config = (
            session.market_data if selection is None else selection.market_data_config()
        )
        source = self._source_factory(
            BinancePublicEndpoints.for_market_type(session.config.market_type)
        )
        runtime = self._runtime_factory(
            config=config,
            warm_up_count=1,
            source=source,
            sink=_ChartOnlySink(publish_completed),
            on_open_candle=publish_in_progress,
            on_transition=_state_reporter(publish_state),
        )
        with self._lock:
            self._runtime = runtime
            self._loop = asyncio.get_running_loop()
        _LOGGER.info(
            "chart.feed.started",
            extra={
                "event_name": "chart.feed.started",
                "symbol": config.symbol,
                "timeframe": config.timeframe,
            },
        )
        try:
            await runtime.run()
        finally:
            with self._lock:
                if self._runtime is runtime:
                    self._runtime = None
                    self._loop = None
            _LOGGER.info(
                "chart.feed.stopped",
                extra={
                    "event_name": "chart.feed.stopped",
                    "symbol": config.symbol,
                    "timeframe": config.timeframe,
                },
            )


def _state_reporter(
    publish_state: Callable[[ChartFeedState], None],
) -> Callable[[MarketDataRuntimeSnapshot], None]:
    """Report only the states a chart has something to say about.

    STARTING and WARMING_UP are the feed connecting for the first time, which
    the chart is already showing as a load in progress; calling that a lost
    connection would announce a fault on every start.
    """

    def report(snapshot: MarketDataRuntimeSnapshot) -> None:
        state = _FEED_STATES.get(snapshot.state)
        if state is None:
            return
        _publish_safely(publish_state, state)

    return report


def _publish_safely(
    publish_state: Callable[[ChartFeedState], None],
    state: ChartFeedState,
) -> None:
    """A chart that cannot be told is never a reason to stop the feed."""
    try:
        publish_state(state)
    except Exception:
        return
