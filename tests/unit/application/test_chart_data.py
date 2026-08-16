from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest

from tests.support.chart_candles import candle_run, flat_candle
from tiewtrade.application.chart_data import (
    MAXIMUM_VISIBLE_CANDLE_COUNT,
    MINIMUM_VISIBLE_CANDLE_COUNT,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
    append_completed_candle,
    default_chart_range,
    latest_completed_boundary,
    visible_candle_count,
    zoomed_chart_range,
)
from tiewtrade.application.chart_indicators import (
    AtrSeries,
    AtrState,
    RsiSeries,
    RsiState,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.spot_policy import SpotTradingPolicy
from tiewtrade.trading.trade_history import FillSide, FillSource, TradeFill


def test_ready_snapshot_rejects_candle_from_another_session_timeframe() -> None:
    with pytest.raises(ValueError, match="candle timeframe must match Session"):
        ready_chart_snapshot(candles=(candle("15m"),), fills=())


def test_snapshot_rejects_partial_candle_facts_without_ohlcv() -> None:
    class PartialCandle:
        symbol = "BTCUSDT"
        timeframe = "5m"
        open_time = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
        close_time = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)

    with pytest.raises(ValueError, match="candles must be Candle values"):
        ready_chart_snapshot(candles=(cast(Candle, PartialCandle()),), fills=())


def test_snapshot_separates_completed_and_forming_candle() -> None:
    completed = candle("5m", minute=15)
    forming = candle("5m", minute=20)

    snapshot = ready_chart_snapshot(
        candles=(completed,),
        in_progress_candle=forming,
    )

    assert snapshot.candles == (completed,)
    assert snapshot.in_progress_candle == forming
    assert snapshot.display_candles == (completed, forming)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (ChartReadState.LOADING, None),
        (ChartReadState.UNAVAILABLE, "Chart is unavailable"),
    ],
)
def test_snapshot_rejects_forming_candle_outside_display_states(
    state: ChartReadState,
    message: str | None,
) -> None:
    with pytest.raises(
        ValueError,
        match="in_progress_candle requires a ready or empty ChartSnapshot",
    ):
        ChartSnapshot(
            session=session(),
            chart_range=chart_range(),
            observed_at_utc=datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
            candles=(),
            fills=(),
            state=state,
            message=message,
            in_progress_candle=candle("5m", minute=20),
        )


@pytest.mark.parametrize(
    ("timeframe", "minute", "observed_minute", "message"),
    [
        ("15m", 15, 20, "timeframe"),
        ("5m", 15, 20, "ChartRange end"),
        ("5m", 20, 25, "observed"),
    ],
)
def test_snapshot_validates_forming_candle_session_range_and_observation(
    timeframe: str,
    minute: int,
    observed_minute: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ChartSnapshot(
            session=session(),
            chart_range=chart_range(),
            observed_at_utc=datetime(
                2026,
                1,
                1,
                0,
                observed_minute,
                tzinfo=UTC,
            ),
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
            in_progress_candle=candle(timeframe, minute=minute),
        )


def test_append_completed_candle_replaces_same_open_time_and_keeps_order() -> None:
    snapshot = ready_chart_snapshot(candles=(candle("5m", minute=15),))

    result = append_completed_candle(snapshot, candle("5m", minute=10))

    assert tuple(item.open_time for item in result.candles) == tuple(
        sorted(item.open_time for item in result.candles)
    )
    replacement = append_completed_candle(result, candle("5m", minute=10, close="105"))
    assert len(replacement.candles) == 2
    assert replacement.candles[0].close == Decimal("105")


def test_chart_range_requires_an_ordered_utc_half_open_range() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="start must use UTC"):
        ChartRange(datetime(2026, 1, 1), start + timedelta(minutes=5))
    with pytest.raises(ValueError, match="start must be before end"):
        ChartRange(start, start)


def test_default_chart_range_ends_at_latest_completed_timeframe_boundary() -> None:
    observed_at_utc = datetime(2026, 1, 1, 10, 3, tzinfo=UTC)
    completed_end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    assert latest_completed_boundary("5m", observed_at_utc) == completed_end
    assert default_chart_range(session(), observed_at_utc) == ChartRange(
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        completed_end,
    )


def test_zoom_keeps_the_range_end_anchored_so_live_candles_stay_visible() -> None:
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    chart_range = ChartRange(end - timedelta(minutes=5) * 120, end)

    zoomed_in = zoomed_chart_range(chart_range, "5m", zoom_in=True)
    zoomed_out = zoomed_chart_range(chart_range, "5m", zoom_in=False)

    assert zoomed_in.end == end
    assert zoomed_out.end == end
    assert visible_candle_count(zoomed_in, "5m") == 96
    assert visible_candle_count(zoomed_out, "5m") == 150


def test_zoom_round_trip_returns_to_a_comparable_window() -> None:
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    chart_range = ChartRange(end - timedelta(minutes=5) * 120, end)

    restored = zoomed_chart_range(
        zoomed_chart_range(chart_range, "5m", zoom_in=True), "5m", zoom_in=False
    )

    assert visible_candle_count(restored, "5m") == 120


def test_zoom_stops_at_the_minimum_and_maximum_window() -> None:
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    interval = timedelta(minutes=5)
    narrowest = ChartRange(end - interval * MINIMUM_VISIBLE_CANDLE_COUNT, end)
    widest = ChartRange(end - interval * MAXIMUM_VISIBLE_CANDLE_COUNT, end)

    assert zoomed_chart_range(narrowest, "5m", zoom_in=True) == narrowest
    assert zoomed_chart_range(widest, "5m", zoom_in=False) == widest


def test_zoom_never_overshoots_the_configured_window_limits() -> None:
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    interval = timedelta(minutes=5)
    near_minimum = ChartRange(end - interval * 22, end)
    near_maximum = ChartRange(end - interval * 480, end)

    assert (
        visible_candle_count(zoomed_chart_range(near_minimum, "5m", zoom_in=True), "5m")
        == MINIMUM_VISIBLE_CANDLE_COUNT
    )
    assert (
        visible_candle_count(
            zoomed_chart_range(near_maximum, "5m", zoom_in=False), "5m"
        )
        == MAXIMUM_VISIBLE_CANDLE_COUNT
    )


def test_zoom_in_never_widens_a_window_already_below_the_minimum() -> None:
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    narrow = ChartRange(end - timedelta(minutes=5) * 4, end)

    assert zoomed_chart_range(narrow, "5m", zoom_in=True) == narrow
    assert (
        visible_candle_count(zoomed_chart_range(narrow, "5m", zoom_in=False), "5m") == 5
    )


def test_zoom_always_changes_the_window_by_at_least_one_candle() -> None:
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    # 4 candles would round back to itself with plain 1.25x integer math.
    chart_range = ChartRange(end - timedelta(minutes=5) * 21, end)

    assert (
        visible_candle_count(zoomed_chart_range(chart_range, "5m", zoom_in=False), "5m")
        == 26
    )
    assert (
        visible_candle_count(zoomed_chart_range(chart_range, "5m", zoom_in=True), "5m")
        == 20
    )


def test_snapshot_reads_immutable_chart_facts_from_configured_paper_session() -> None:
    snapshot = ready_chart_snapshot()

    assert snapshot.session_id == session().config.session_id
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.timeframe == "5m"
    assert "session" not in ChartSnapshot.__slots__


def test_snapshot_rejects_unknown_read_state() -> None:
    with pytest.raises(ValueError, match="state must be a ChartReadState"):
        ChartSnapshot(
            session=session(),
            chart_range=chart_range(),
            observed_at_utc=datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
            candles=(),
            fills=(),
            state="ready",  # type: ignore[arg-type]
        )


def test_snapshot_rejects_range_ending_after_observation_boundary() -> None:
    with pytest.raises(
        ValueError, match="ChartRange end must not be after observed_at_utc"
    ):
        ChartSnapshot(
            session=session(),
            chart_range=chart_range(),
            observed_at_utc=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
            candles=(),
            fills=(),
            state=ChartReadState.READY,
        )


def test_snapshot_rejects_candle_not_completed_by_observation_boundary() -> None:
    with pytest.raises(ValueError, match="candle must be completed by observed_at_utc"):
        ChartSnapshot(
            session=session(),
            chart_range=ChartRange(
                datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
            ),
            observed_at_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
            candles=(candle("5m", minute=10),),
            fills=(),
            state=ChartReadState.READY,
        )


def test_ready_snapshot_derives_session_scoped_markers_from_durable_fills() -> None:
    fill = trade_fill(filled_at_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC))

    snapshot = ready_chart_snapshot(candles=(candle("5m", minute=10),), fills=(fill,))

    assert snapshot.state is ChartReadState.READY
    assert len(snapshot.markers) == 1
    assert snapshot.markers[0].fill_id == fill.fill_id
    assert snapshot.markers[0].side is FillSide.BUY
    assert snapshot.markers[0].price == fill.price
    assert snapshot.markers[0].filled_at_utc == fill.filled_at_utc


def test_entry_marks_the_candle_it_filled_on() -> None:
    fill = trade_fill(filled_at_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC))

    snapshot = ready_chart_snapshot(candles=(candle("5m", minute=10),), fills=(fill,))

    assert fill.entry_number is not None
    assert snapshot.markers[0].candle_open_time == datetime(
        2026, 1, 1, 0, 10, tzinfo=UTC
    )


def test_exit_marks_the_candle_that_reached_its_target_not_the_one_after() -> None:
    """An exit is stamped at its candle's close, which is the next candle's open."""
    exit_fill = replace(
        trade_fill(filled_at_utc=datetime(2026, 1, 1, 0, 15, tzinfo=UTC)),
        side=FillSide.SELL,
        entry_number=None,
    )

    snapshot = ready_chart_snapshot(
        candles=(candle("5m", minute=10),),
        fills=(exit_fill,),
    )

    assert snapshot.markers[0].candle_open_time == datetime(
        2026, 1, 1, 0, 10, tzinfo=UTC
    )


def test_exit_taken_mid_candle_marks_the_candle_it_filled_in() -> None:
    """An exit that did not wait for the close is stamped where it happened."""
    exit_fill = replace(
        trade_fill(filled_at_utc=datetime(2026, 1, 1, 0, 22, tzinfo=UTC)),
        side=FillSide.SELL,
        entry_number=None,
    )

    snapshot = ready_chart_snapshot(
        candles=(candle("5m", minute=10),),
        fills=(exit_fill,),
        in_progress_candle=candle("5m", minute=20),
    )

    # The forming candle it filled in, not the completed one before it.
    assert snapshot.markers[0].candle_open_time == datetime(
        2026, 1, 1, 0, 20, tzinfo=UTC
    )


def test_short_entry_sells_without_being_read_as_an_exit() -> None:
    """Side cannot tell Entry from exit: a Futures short opens with a SELL."""
    short_entry = replace(
        trade_fill(filled_at_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC)),
        side=FillSide.SELL,
        entry_number=2,
    )

    snapshot = ready_chart_snapshot(
        candles=(candle("5m", minute=10),),
        fills=(short_entry,),
    )

    assert snapshot.markers[0].candle_open_time == datetime(
        2026, 1, 1, 0, 10, tzinfo=UTC
    )


def test_ready_snapshot_marks_a_fill_inside_the_forming_candle() -> None:
    fill = trade_fill(filled_at_utc=datetime(2026, 1, 1, 0, 22, tzinfo=UTC))

    snapshot = ready_chart_snapshot(
        candles=(candle("5m", minute=10),),
        fills=(fill,),
        in_progress_candle=candle("5m", minute=20),
    )

    assert snapshot.visible_range == ChartRange(
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
    )
    assert [marker.fill_id for marker in snapshot.markers] == [fill.fill_id]


def test_ready_snapshot_rejects_fill_from_another_session_or_outside_range() -> None:
    other_session_fill = trade_fill(session_id=uuid4())
    out_of_range_fill = trade_fill(
        filled_at_utc=datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    )

    with pytest.raises(ValueError, match="fill session must match Session"):
        ready_chart_snapshot(fills=(other_session_fill,))
    with pytest.raises(ValueError, match="fill must be inside the visible range"):
        ready_chart_snapshot(fills=(out_of_range_fill,))


def test_snapshot_rejects_rsi_for_a_candle_it_does_not_hold() -> None:
    with pytest.raises(ValueError, match="RSI open_time must match"):
        ready_chart_snapshot(
            candles=(candle("5m", minute=0),),
            rsi=RsiSeries(
                values={datetime(2026, 1, 1, 0, 5, tzinfo=UTC): Decimal("55")}
            ),
        )


def test_snapshot_rejects_atr_for_a_candle_it_does_not_hold() -> None:
    with pytest.raises(ValueError, match="ATR open_time must match"):
        ready_chart_snapshot(
            candles=(candle("5m", minute=0),),
            atr=AtrSeries(
                values={datetime(2026, 1, 1, 0, 5, tzinfo=UTC): Decimal("12")}
            ),
        )


def test_loading_snapshot_cannot_carry_indicators() -> None:
    with pytest.raises(ValueError, match="cannot carry indicators"):
        ChartSnapshot(
            session=session(),
            chart_range=chart_range(),
            observed_at_utc=datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
            candles=(),
            fills=(),
            state=ChartReadState.LOADING,
            rsi=RsiSeries(state=RsiState(period=14, previous_close=Decimal("100"))),
        )

    with pytest.raises(ValueError, match="cannot carry indicators"):
        ChartSnapshot(
            session=session(),
            chart_range=chart_range(),
            observed_at_utc=datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
            candles=(),
            fills=(),
            state=ChartReadState.LOADING,
            atr=AtrSeries(state=AtrState(period=14)),
        )


def test_forming_candle_reads_rsi_without_committing_it() -> None:
    warm_up = candle_run(20, last_open_time=datetime(2026, 1, 1, 0, 15, tzinfo=UTC))
    series = RsiSeries.over(warm_up)
    latest = warm_up[-1]
    forming = flat_candle(datetime(2026, 1, 1, 0, 20, tzinfo=UTC), "140")

    snapshot = ready_chart_snapshot(
        candles=(latest,),
        in_progress_candle=forming,
        rsi=series.restricted_to((latest.open_time,)),
    )

    assert snapshot.rsi_for(forming.open_time) == series.value_for_close(forming.close)
    assert snapshot.rsi_for(latest.open_time) == series.value_at(latest.open_time)
    # The forming value is derived, never stored, so the committed series is
    # untouched and stays ready for the candle that actually closes.
    assert forming.open_time not in snapshot.rsi.values
    assert snapshot.rsi.state == series.state


def test_append_completed_candle_extends_the_rsi_series() -> None:
    warm_up = candle_run(20, last_open_time=datetime(2026, 1, 1, 0, 5, tzinfo=UTC))
    series = RsiSeries.over(warm_up)
    latest = warm_up[-1]
    snapshot = ready_chart_snapshot(
        candles=(latest,),
        rsi=series.restricted_to((latest.open_time,)),
    )
    closed = flat_candle(datetime(2026, 1, 1, 0, 10, tzinfo=UTC), "90")

    appended = append_completed_candle(snapshot, closed)

    expected = RsiSeries.over((*warm_up, closed))
    assert appended.rsi.value_at(closed.open_time) == expected.value_at(
        closed.open_time
    )
    assert appended.rsi.state == expected.state


def test_append_completed_candle_leaves_rsi_alone_when_restating_a_candle() -> None:
    warm_up = candle_run(20, last_open_time=datetime(2026, 1, 1, 0, 15, tzinfo=UTC))
    series = RsiSeries.over(warm_up)
    latest = warm_up[-1]
    snapshot = ready_chart_snapshot(
        candles=(latest,),
        rsi=series.restricted_to((latest.open_time,)),
    )

    # Wilder smoothing is path dependent, so a restated candle must not be
    # folded into the series a second time.
    appended = append_completed_candle(snapshot, latest)

    assert appended.rsi == snapshot.rsi


def ready_chart_snapshot(
    *,
    candles: tuple[Candle, ...] = (),
    fills: tuple[TradeFill, ...] = (),
    in_progress_candle: Candle | None = None,
    rsi: RsiSeries | None = None,
    atr: AtrSeries | None = None,
) -> ChartSnapshot:
    return ChartSnapshot(
        session=session(),
        chart_range=chart_range(),
        observed_at_utc=datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
        candles=candles,
        fills=fills,
        state=ChartReadState.READY,
        in_progress_candle=in_progress_candle,
        rsi=rsi or RsiSeries(),
        atr=atr or AtrSeries(),
    )


def chart_range() -> ChartRange:
    return ChartRange(
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
    )


def session() -> ConfiguredPaperSession:
    return ConfiguredPaperSession(
        config=SessionConfig(
            session_id=UUID("00000000-0000-0000-0000-000000000138"),
            preset_version="rsi-step-grid-v1",
            market_type=MarketType.SPOT,
            trade_mode=TradeMode.PAPER,
            available_capital=Decimal("1000"),
            entry_policy=EntryPolicy(max_entries=10),
            spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.8")),
        ),
        market_data=MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        created_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )


def candle(
    timeframe: str,
    *,
    minute: int = 0,
    close: str = "101",
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("106"),
        low=Decimal("99"),
        close=Decimal(close),
    )


def trade_fill(
    *,
    session_id: UUID | None = None,
    filled_at_utc: datetime | None = None,
) -> TradeFill:
    return TradeFill(
        fill_id="fill-138",
        basket_id=uuid4(),
        session_id=session().config.session_id if session_id is None else session_id,
        order_id="order-138",
        exchange_trade_id="trade-138",
        side=FillSide.BUY,
        entry_number=1,
        filled_at_utc=(
            datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
            if filled_at_utc is None
            else filled_at_utc
        ),
        price=Decimal("101.25"),
        quantity=Decimal("0.1"),
        notional=Decimal("10.125"),
        realized_pnl=Decimal("0"),
        source=FillSource.PAPER_EXECUTOR,
    )
