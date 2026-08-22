import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread
from typing import cast

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.chart_realtime_feed import ChartOnlyFeedController
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.latest_market_price import LatestMarketPrice
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_pipeline import MarketDataCandleSink
from tiewtrade.market_data.candle_source import MarketDataCandleSource


def test_start_publishes_current_candle_without_bot_runtime() -> None:
    completed: list[Candle] = []
    forming: list[Candle] = []
    delivered = Event()

    def publish_forming(value: Candle) -> None:
        forming.append(value)
        delivered.set()

    controller = ChartOnlyFeedController(
        source_factory=lambda endpoints: cast(MarketDataCandleSource, object()),
        runtime_factory=lambda **kwargs: FakeRuntime(**kwargs),
    )

    controller.start(configured_spot_session(), completed.append, publish_forming)

    assert delivered.wait(timeout=1)

    assert forming == [candle(20)]
    assert completed == [candle(15)]


def test_latest_price_requires_current_session_and_fresh_chart_fact() -> None:
    observed_at = datetime(2026, 1, 1, 0, 21, tzinfo=UTC)
    session = configured_spot_session()
    delivered = Event()
    latest_market_price = LatestMarketPrice(clock=lambda: observed_at)
    controller = ChartOnlyFeedController(
        source_factory=lambda endpoints: cast(MarketDataCandleSource, object()),
        runtime_factory=lambda **kwargs: FakeRuntime(**kwargs),
        latest_market_price=latest_market_price,
    )

    controller.start(session, lambda _candle: None, lambda _candle: delivered.set())
    assert delivered.wait(timeout=1)

    assert latest_market_price.get(
        session.config.session_id,
        max_age=timedelta(seconds=30),
    ) == Decimal("100")

    observed_at += timedelta(seconds=31)

    with pytest.raises(RuntimeError, match="fresh market price"):
        latest_market_price.get(
            session.config.session_id,
            max_age=timedelta(seconds=30),
        )


def test_different_chart_view_does_not_replace_session_market_price() -> None:
    session = configured_spot_session()
    delivered = Event()
    latest_market_price = LatestMarketPrice()
    controller = ChartOnlyFeedController(
        source_factory=lambda endpoints: cast(MarketDataCandleSource, object()),
        runtime_factory=lambda **kwargs: FakeRuntime(**kwargs),
        latest_market_price=latest_market_price,
    )

    controller.start(
        session,
        lambda _candle: None,
        lambda _candle: delivered.set(),
        ChartViewSelection(symbol="ETHUSDT", timeframe="1m"),
    )

    assert delivered.wait(timeout=1)
    with pytest.raises(RuntimeError, match="fresh market price"):
        latest_market_price.get(
            session.config.session_id,
            max_age=timedelta(seconds=30),
        )


def test_stop_swallows_cancelled_error_from_a_shutdown_race() -> None:
    """runtime.stop() can lose a race with its own loop's shutdown teardown.

    When the runtime's run() already exited (e.g. it failed closed), the
    background loop can cancel a just-scheduled stop() task before it runs,
    surfacing as concurrent.futures.CancelledError from result(). stop()
    must treat that as "already stopped", not crash the caller.
    """

    class CancellingRuntime:
        async def run(self) -> None:
            pass

        async def stop(self) -> None:
            raise asyncio.CancelledError()

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    try:
        controller = ChartOnlyFeedController(
            source_factory=lambda endpoints: cast(MarketDataCandleSource, object()),
        )
        controller._runtime = CancellingRuntime()
        controller._loop = loop

        controller.stop()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=1)


class FakeRuntime:
    def __init__(
        self,
        *,
        sink: MarketDataCandleSink,
        on_open_candle: Callable[[Candle], None],
        **kwargs: object,
    ) -> None:
        del kwargs
        self._sink = sink
        self._on_open_candle = on_open_candle

    async def run(self) -> None:
        await self._sink.process_completed(
            candle(15), received_at=datetime.now(UTC), backfilled=False
        )
        self._on_open_candle(candle(20))

    async def stop(self) -> None:
        pass


def candle(minute: int) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
    )
