"""Shaping a chart snapshot: the three rules, without a chart to draw them on."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from tests.support.chart_candles import flat_candle
from tests.support.trade_history_records import trade_fill
from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.trading.trade_history import TradeFill
from tiewtrade.ui.chart_snapshots import (
    ViewFacts,
    answers_the_view,
    blank_snapshot,
    fills_inside,
    observed_at_for,
    unavailable_snapshot,
    with_in_progress_candle,
    without_fills,
)

FACTS: ViewFacts = (UUID("00000000-0000-0000-0000-0000000000a1"), "BTCUSDT", "5m")
START = datetime(2026, 8, 1, 12, tzinfo=UTC)
RANGE = ChartRange(START, START + timedelta(minutes=30))


def _loaded(*fills: TradeFill) -> ChartSnapshot:
    return ChartSnapshot(
        chart_range=RANGE,
        observed_at_utc=RANGE.end,
        candles=(),
        fills=fills,
        state=ChartReadState.READY,
        _session_facts=FACTS,
    )


def test_a_window_ending_in_the_future_is_not_read_as_one_already_passed() -> None:
    """The clock has not reached the end of the range, so the range wins."""
    now = RANGE.end - timedelta(minutes=10)

    assert observed_at_for(RANGE, now=now) == RANGE.end


def test_a_window_the_clock_has_passed_is_observed_now() -> None:
    now = RANGE.end + timedelta(minutes=10)

    assert observed_at_for(RANGE, now=now) == now


def test_only_the_fills_inside_the_visible_span_are_kept() -> None:
    inside = trade_fill(fill_id="inside", filled_at_utc=START + timedelta(minutes=5))
    after = trade_fill(fill_id="after", filled_at_utc=RANGE.end)
    before = trade_fill(fill_id="before", filled_at_utc=START - timedelta(minutes=1))

    assert fills_inside((before, inside, after), RANGE) == (inside,)


def test_dropping_the_forming_candle_drops_the_fills_inside_it() -> None:
    """Taking it off shortens the span, so a fill in it has to go with it."""
    forming = flat_candle(RANGE.end, 100)
    in_the_forming_candle = trade_fill(
        fill_id="forming", filled_at_utc=RANGE.end + timedelta(seconds=30)
    )
    with_it = with_in_progress_candle(
        _loaded(),
        now=RANGE.end,
        in_progress_candle=forming,
        fills=(in_the_forming_candle,),
    )
    assert with_it.fills == (in_the_forming_candle,)

    without_it = with_in_progress_candle(
        with_it, now=RANGE.end, in_progress_candle=None
    )

    assert without_it.fills == ()
    assert without_it.in_progress_candle is None


def test_a_chart_of_another_pair_carries_no_fills() -> None:
    """Fills are read by Session, not by Pair, so they would badge the wrong chart."""
    other = (UUID("00000000-0000-0000-0000-0000000000a1"), "ETHUSDT", "5m")

    inside = trade_fill(filled_at_utc=START + timedelta(minutes=5))

    stripped = without_fills(_loaded(inside), facts=other)

    assert stripped.fills == ()
    assert stripped.symbol == "ETHUSDT"


def test_a_blank_chart_carries_the_view_it_was_asked_for() -> None:
    blank = blank_snapshot(FACTS, RANGE, now=RANGE.end, state=ChartReadState.LOADING)

    assert (blank.session_id, blank.symbol, blank.timeframe) == FACTS
    assert blank.candles == ()
    assert blank.state is ChartReadState.LOADING


def test_an_unavailable_chart_says_so() -> None:
    unavailable = unavailable_snapshot(FACTS, RANGE, now=RANGE.end)

    assert unavailable.state is ChartReadState.UNAVAILABLE
    assert unavailable.message == "Chart is unavailable"


def test_a_read_of_the_view_on_screen_is_answered() -> None:
    assert answers_the_view(_loaded(), facts=FACTS, chart_range=RANGE)


def test_a_read_of_a_view_nobody_is_looking_at_is_not() -> None:
    """A view changed while a read was out leaves the reply describing nothing."""
    other_pair = (FACTS[0], "ETHUSDT", "5m")
    other_range = ChartRange(RANGE.end, RANGE.end + timedelta(minutes=30))

    assert not answers_the_view(_loaded(), facts=other_pair, chart_range=RANGE)
    assert not answers_the_view(_loaded(), facts=FACTS, chart_range=other_range)
    assert not answers_the_view(_loaded(), facts=None, chart_range=RANGE)
    assert not answers_the_view("not a snapshot", facts=FACTS, chart_range=RANGE)


def test_a_price_that_is_not_a_snapshot_is_never_the_answer() -> None:
    assert not answers_the_view(Decimal("100"), facts=FACTS, chart_range=RANGE)
