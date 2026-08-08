from datetime import UTC, datetime
from decimal import Decimal
from threading import Event

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.chart_realtime_feed import ChartOnlyFeedController
from tiewtrade.market_data.candle import Candle


def test_start_publishes_current_candle_without_bot_runtime() -> None:
    completed: list[Candle] = []
    forming: list[Candle] = []
    delivered = Event()

    def publish_forming(value: Candle) -> None:
        forming.append(value)
        delivered.set()

    controller = ChartOnlyFeedController(
        source_factory=lambda endpoints: object(),  # type: ignore[arg-type]
        runtime_factory=lambda **kwargs: FakeRuntime(**kwargs),
    )

    controller.start(configured_spot_session(), completed.append, publish_forming)

    assert delivered.wait(timeout=1)

    assert forming == [candle(20)]
    assert completed == [candle(15)]


class FakeRuntime:
    def __init__(
        self,
        *,
        sink: object,
        on_open_candle: object,
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
