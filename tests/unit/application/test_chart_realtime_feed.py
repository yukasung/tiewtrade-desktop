from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event
from typing import cast

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.chart_realtime_feed import ChartOnlyFeedController
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
        await self._sink.process_completed(candle(15), received_at=datetime.now(UTC))
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
