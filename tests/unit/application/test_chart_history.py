import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from tests.support.chart_candles import FIVE_MINUTES, candle_run, flat_candle
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.chart_history import ChartHistory
from tiewtrade.application.chart_indicators import (
    RsiSeries,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.indicators import WILDER_WARM_UP_CANDLES
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.trade_history import FillSide, FillSource, TradeFill


class FakeLoadChartCandles:
    def __init__(
        self,
        candles: tuple[Candle, ...],
        *,
        range_aware: bool = False,
    ) -> None:
        self._candles = candles
        # Off by default so a test can hand back candles the range does not
        # cover; on when the test depends on the requested range narrowing.
        self._range_aware = range_aware
        self.requests: list[tuple[ConfiguredPaperSession, ChartRange]] = []

    async def __call__(
        self,
        configured: ConfiguredPaperSession,
        requested: ChartRange,
    ) -> tuple[Candle, ...]:
        self.requests.append((configured, requested))
        if not self._range_aware:
            return self._candles
        return tuple(
            item
            for item in self._candles
            if requested.start <= item.open_time and item.close_time <= requested.end
        )


class FakeListChartFills:
    def __init__(self, fills: tuple[TradeFill, ...]) -> None:
        self._fills = fills
        self.requests: list[tuple[ConfiguredPaperSession, ChartRange]] = []

    def __call__(
        self,
        session: ConfiguredPaperSession,
        requested: ChartRange,
    ) -> tuple[TradeFill, ...]:
        self.requests.append((session, requested))
        return self._fills


def test_chart_history_loads_only_requested_public_range_and_session_fills() -> None:
    selected_range = chart_range()
    buy = trade_fill("buy-1", FillSide.BUY)
    sell = trade_fill("sell-1", FillSide.SELL)
    load_candles = FakeLoadChartCandles((candle(),))
    list_fills = FakeListChartFills((buy, sell))
    history = ChartHistory(
        load_candles=load_candles,
        list_fills=list_fills,
        clock=lambda: selected_range.end,
    )

    snapshot = asyncio.run(history.load(session(), selected_range))

    assert load_candles.requests == [(session(), warm_up_range(selected_range))]
    assert list_fills.requests == [(session(), selected_range)]
    assert snapshot.state is ChartReadState.READY
    assert [marker.fill_id for marker in snapshot.markers] == ["buy-1", "sell-1"]


def test_chart_history_delegates_each_load_to_focused_candle_callable() -> None:
    selected_range = chart_range()
    load_candles = FakeLoadChartCandles((candle(),))
    history = ChartHistory(
        load_candles=load_candles,
        list_fills=FakeListChartFills(()),
        clock=lambda: selected_range.end,
    )

    asyncio.run(history.load(session(), selected_range))
    asyncio.run(history.load(session(), selected_range))

    assert load_candles.requests == [
        (session(), warm_up_range(selected_range)),
        (session(), warm_up_range(selected_range)),
    ]


def test_refresh_completed_candle_advances_latest_range_and_reloads_durable_fills() -> (
    None
):
    selected_range = chart_range()
    completed = candle(20)
    new_fill = trade_fill("runtime-buy", FillSide.BUY)
    fills = FakeListChartFills((new_fill,))
    history = ChartHistory(
        load_candles=FakeLoadChartCandles(()),
        list_fills=fills,
        clock=lambda: completed.close_time,
    )
    current = ChartSnapshot(
        session=session(),
        chart_range=selected_range,
        observed_at_utc=selected_range.end,
        candles=(candle(0), candle(5), candle(10), candle(15)),
        fills=(),
        state=ChartReadState.READY,
    )

    refreshed = asyncio.run(history.refresh_completed(session(), current, completed))

    assert refreshed.chart_range == ChartRange(
        datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
    )
    assert [item.open_time.minute for item in refreshed.candles] == [5, 10, 15, 20]
    assert fills.requests == [(session(), refreshed.chart_range)]
    assert [marker.fill_id for marker in refreshed.markers] == ["runtime-buy"]


def test_refresh_after_candle_gap_reloads_latest_bounded_range_and_durable_fills() -> (
    None
):
    selected_range = chart_range()
    completed = candle(25)
    latest_range = ChartRange(
        datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
    )
    loaded_candles = (candle(10), candle(15), candle(20))
    latest_candles = (*loaded_candles, completed)
    load_candles = FakeLoadChartCandles(loaded_candles)
    new_fill = trade_fill("runtime-gap-buy", FillSide.BUY)
    fills = FakeListChartFills((new_fill,))
    history = ChartHistory(
        load_candles=load_candles,
        list_fills=fills,
        clock=lambda: completed.close_time,
    )
    current = ChartSnapshot(
        session=session(),
        chart_range=selected_range,
        observed_at_utc=selected_range.end,
        candles=(candle(0), candle(5), candle(10), candle(15)),
        fills=(),
        state=ChartReadState.READY,
    )

    refreshed = asyncio.run(history.refresh_completed(session(), current, completed))

    assert load_candles.requests == [(session(), warm_up_range(latest_range))]
    assert fills.requests == [(session(), latest_range)]
    assert refreshed.chart_range == latest_range
    assert refreshed.candles == latest_candles
    assert [marker.fill_id for marker in refreshed.markers] == ["runtime-gap-buy"]


def test_warm_up_candles_seed_rsi_without_reaching_the_snapshot() -> None:
    visible = ChartRange(
        datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 1, 20, tzinfo=UTC),
    )
    loaded = candle_run(40, last_open_time=visible.end - FIVE_MINUTES)
    history = ChartHistory(
        load_candles=FakeLoadChartCandles(loaded),
        list_fills=FakeListChartFills(()),
        clock=lambda: visible.end,
    )

    snapshot = asyncio.run(history.load(session(), visible))

    visible_candles = tuple(item for item in loaded if item.open_time >= visible.start)
    assert snapshot.candles == visible_candles
    assert len(snapshot.candles) < len(loaded)
    expected = RsiSeries.over(loaded)
    assert dict(snapshot.rsi.values) == {
        item.open_time: expected.value_at(item.open_time)
        for item in visible_candles
        if expected.value_at(item.open_time) is not None
    }


def test_rsi_of_a_candle_holds_its_displayed_value_when_the_window_narrows() -> None:
    """Zooming must not move a candle's RSI, which is why warm-up exists.

    A narrower window seeds from a different candle, so Wilder smoothing
    converges rather than matching bit for bit. Warm-up has to push that
    residue far below the two decimals the chart shows.
    """
    wide = ChartRange(
        datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 1, 20, tzinfo=UTC),
    )
    narrow = ChartRange(
        datetime(2026, 1, 1, 1, 10, tzinfo=UTC),
        datetime(2026, 1, 1, 1, 20, tzinfo=UTC),
    )
    shared = datetime(2026, 1, 1, 1, 15, tzinfo=UTC)
    history = ChartHistory(
        load_candles=FakeLoadChartCandles(
            candle_run(400, last_open_time=wide.end - FIVE_MINUTES),
            range_aware=True,
        ),
        list_fills=FakeListChartFills(()),
        clock=lambda: wide.end,
    )

    wide_snapshot = asyncio.run(history.load(session(), wide))
    narrow_snapshot = asyncio.run(history.load(session(), narrow))

    wide_value = wide_snapshot.rsi.value_at(shared)
    narrow_value = narrow_snapshot.rsi.value_at(shared)
    assert wide_value is not None and narrow_value is not None
    displayed = Decimal("0.01")
    assert wide_value.quantize(displayed) == narrow_value.quantize(displayed)
    assert abs(wide_value - narrow_value) < Decimal("0.0001")


def test_refresh_inside_the_range_extends_rsi_without_reloading_history() -> None:
    visible = ChartRange(
        datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 1, 20, tzinfo=UTC),
    )
    loaded = candle_run(40, last_open_time=datetime(2026, 1, 1, 1, 5, tzinfo=UTC))
    load_candles = FakeLoadChartCandles(loaded)
    history = ChartHistory(
        load_candles=load_candles,
        list_fills=FakeListChartFills(()),
        clock=lambda: visible.end,
    )
    current = asyncio.run(history.load(session(), visible))
    completed = flat_candle(datetime(2026, 1, 1, 1, 10, tzinfo=UTC), "108")

    refreshed = asyncio.run(history.refresh_completed(session(), current, completed))

    assert len(load_candles.requests) == 1
    expected = RsiSeries.over((*loaded, completed))
    assert refreshed.rsi.value_at(completed.open_time) == expected.value_at(
        completed.open_time
    )


def chart_range() -> ChartRange:
    return ChartRange(
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
    )


def warm_up_range(visible: ChartRange) -> ChartRange:
    """The wider range read so each candle's RSI stays stable across zoom."""
    return ChartRange(
        visible.start - session().market_data.interval * WILDER_WARM_UP_CANDLES,
        visible.end,
    )


def session() -> ConfiguredPaperSession:
    return ConfiguredPaperSession(
        config=SessionConfig(
            session_id=UUID("00000000-0000-0000-0000-000000000138"),
            preset_version="rsi-step-grid-v1",
            market_type=MarketType.SPOT,
            trade_mode=TradeMode.PAPER,
            capital_per_entry=Decimal("1000"),
            entry_policy=EntryPolicy(max_entries=10),
        ),
        market_data=MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        created_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )


def candle(minute: int = 10) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("106"),
        low=Decimal("99"),
        close=Decimal("101"),
    )


def trade_fill(fill_id: str, side: FillSide) -> TradeFill:
    return TradeFill(
        fill_id=fill_id,
        basket_id=UUID("00000000-0000-0000-0000-000000000139"),
        session_id=session().config.session_id,
        order_id=f"order-{fill_id}",
        exchange_trade_id=None,
        side=side,
        entry_number=1 if side is FillSide.BUY else None,
        filled_at_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        price=Decimal("101.25"),
        quantity=Decimal("0.1"),
        notional=Decimal("10.125"),
        realized_pnl=Decimal("0"),
        source=FillSource.PAPER_EXECUTOR,
    )
