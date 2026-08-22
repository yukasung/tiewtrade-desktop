from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.application.chart_indicators import AtrSeries, RsiSeries
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import timeframe_to_interval
from tiewtrade.trading.trade_history import FillSide, TradeFill

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DEFAULT_VISIBLE_CANDLE_COUNT = 120
MINIMUM_VISIBLE_CANDLE_COUNT = 20
MAXIMUM_VISIBLE_CANDLE_COUNT = 500
ChartCandle = Candle


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


@dataclass(frozen=True, slots=True)
class ChartRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_utc(self.start, "start")
        _require_utc(self.end, "end")
        if self.start >= self.end:
            raise ValueError("start must be before end")

    def contains(self, value: datetime) -> bool:
        return self.start <= value < self.end


def latest_completed_boundary(
    timeframe: str,
    observed_at_utc: datetime,
) -> datetime:
    _require_utc(observed_at_utc, "observed_at_utc")
    interval = timeframe_to_interval(timeframe)
    return _UTC_EPOCH + ((observed_at_utc - _UTC_EPOCH) // interval) * interval


def default_chart_range(
    session: ConfiguredPaperSession,
    observed_at_utc: datetime,
) -> ChartRange:
    interval = timeframe_to_interval(session.market_data.timeframe)
    completed_end = latest_completed_boundary(
        session.market_data.timeframe,
        observed_at_utc,
    )
    return ChartRange(
        start=completed_end - interval * _DEFAULT_VISIBLE_CANDLE_COUNT,
        end=completed_end,
    )


def visible_candle_count(chart_range: ChartRange, timeframe: str) -> int:
    """How many whole candles of `timeframe` the range spans."""
    interval = timeframe_to_interval(timeframe)
    return max(1, (chart_range.end - chart_range.start) // interval)


def zoomed_chart_range(
    chart_range: ChartRange,
    timeframe: str,
    *,
    zoom_in: bool,
) -> ChartRange:
    """Widen or narrow the visible window by one 1.25x step.

    The range end stays anchored so that zooming never drops the newest
    candle out of view, which keeps a live session following the latest
    boundary. Returns the same range when a zoom limit is already reached.
    """
    interval = timeframe_to_interval(timeframe)
    visible = visible_candle_count(chart_range, timeframe)
    target = (visible * 4) // 5 if zoom_in else (visible * 5) // 4
    if target == visible:
        target = visible - 1 if zoom_in else visible + 1
    if zoom_in:
        # Clamping must never flip the direction: a window already narrower
        # than the limit stays put instead of widening.
        target = min(max(target, MINIMUM_VISIBLE_CANDLE_COUNT), visible)
    else:
        target = max(min(target, MAXIMUM_VISIBLE_CANDLE_COUNT), visible)
    if target == visible:
        return chart_range
    return ChartRange(chart_range.end - interval * target, chart_range.end)


@dataclass(frozen=True, slots=True)
class ChartMarker:
    fill_id: str
    side: FillSide
    price: Decimal
    filled_at_utc: datetime
    # Which candle the fill belongs to. A timestamp alone cannot say: an Entry
    # fills at its candle's open and an exit at its candle's close, and a close
    # instant is also the next candle's open.
    candle_open_time: datetime

    @classmethod
    def from_fill(cls, fill: TradeFill, timeframe: str) -> "ChartMarker":
        return cls(
            fill_id=fill.fill_id,
            side=fill.side,
            price=fill.price,
            filled_at_utc=fill.filled_at_utc,
            candle_open_time=_filled_candle_open_time(fill, timeframe),
        )

    def __post_init__(self) -> None:
        if not self.fill_id:
            raise ValueError("fill_id must not be empty")
        _require_utc(self.filled_at_utc, "filled_at_utc")
        _require_utc(self.candle_open_time, "candle_open_time")


def _filled_candle_open_time(fill: TradeFill, timeframe: str) -> datetime:
    """The candle that produced this fill.

    An exit taken at a candle's close is stamped with that close, which is
    also the next candle's open, so it belongs to the candle before the
    boundary — the one whose high or low reached the target. An exit taken
    while the candle was still forming is stamped mid-candle and belongs
    where it falls, as does an Entry, which is stamped at its candle's open.
    """
    boundary = latest_completed_boundary(timeframe, fill.filled_at_utc)
    stamped_on_a_boundary = boundary == fill.filled_at_utc
    if fill.entry_number is not None or not stamped_on_a_boundary:
        return boundary
    return boundary - timeframe_to_interval(timeframe)


class ChartReadState(StrEnum):
    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ChartSnapshot:
    chart_range: ChartRange
    observed_at_utc: datetime
    candles: tuple[Candle, ...]
    fills: tuple[TradeFill, ...]
    state: ChartReadState
    in_progress_candle: Candle | None = None
    message: str | None = None
    rsi: RsiSeries = field(default_factory=RsiSeries)
    atr: AtrSeries = field(default_factory=AtrSeries)
    session: InitVar[ConfiguredPaperSession | None] = None
    _session_facts: InitVar[tuple[UUID, str, str] | None] = None
    session_id: UUID = field(init=False)
    symbol: str = field(init=False)
    timeframe: str = field(init=False)
    markers: tuple[ChartMarker, ...] = field(init=False, default=())

    def __post_init__(
        self,
        session: ConfiguredPaperSession | None,
        _session_facts: tuple[UUID, str, str] | None,
    ) -> None:
        session_id, symbol, timeframe = _session_identity(session, _session_facts)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", timeframe)

        _require_session_facts(symbol=symbol, timeframe=timeframe)
        _require_state_matches_data(self)
        _require_in_progress_candle(self)
        _require_completed_candles(self)
        # Reading the fills is also what rejects them, so the markers are
        # taken here rather than after the indicators: a fill that cannot be
        # drawn is the older complaint of the two, and stays the one reported.
        object.__setattr__(self, "markers", _markers_of(self))
        _require_indicators(self)

    @property
    def visible_range(self) -> ChartRange:
        """The span the chart draws: ChartRange plus any forming candle.

        A fill lands on the chart the moment it happens, so it can sit inside
        the candle still forming past the completed range.
        """
        forming = self.in_progress_candle
        if forming is None:
            return self.chart_range
        return ChartRange(self.chart_range.start, forming.close_time)

    @property
    def display_candles(self) -> tuple[Candle, ...]:
        if self.in_progress_candle is None:
            return self.candles
        return (*self.candles, self.in_progress_candle)

    def rsi_for(self, open_time: datetime) -> Decimal | None:
        """RSI of any displayed candle, completed or forming."""
        forming = self.in_progress_candle
        if forming is not None and forming.open_time == open_time:
            return self.rsi.value_for_close(forming.close)
        return self.rsi.value_at(open_time)

    def atr_for(self, open_time: datetime) -> Decimal | None:
        """ATR of any displayed candle, completed or forming."""
        forming = self.in_progress_candle
        if forming is not None and forming.open_time == open_time:
            return self.atr.value_for_candle(forming)
        return self.atr.value_at(open_time)


def append_completed_candle(snapshot: ChartSnapshot, candle: Candle) -> ChartSnapshot:
    if snapshot.state is not ChartReadState.READY:
        raise ValueError("completed candles require a ready ChartSnapshot")
    candles_by_open_time = {item.open_time: item for item in snapshot.candles}
    candles_by_open_time[candle.open_time] = candle
    # Wilder smoothing is path dependent, so only a candle newer than every
    # candle held can extend the series; a restated one leaves it untouched.
    extends = follows(snapshot.candles, candle)
    rsi = snapshot.rsi.advanced(candle) if extends else snapshot.rsi
    atr = snapshot.atr.advanced(candle) if extends else snapshot.atr
    return ChartSnapshot(
        candles=tuple(
            candles_by_open_time[open_time]
            for open_time in sorted(candles_by_open_time)
        ),
        chart_range=snapshot.chart_range,
        observed_at_utc=snapshot.observed_at_utc,
        fills=snapshot.fills,
        state=snapshot.state,
        in_progress_candle=snapshot.in_progress_candle,
        message=snapshot.message,
        rsi=rsi,
        atr=atr,
        _session_facts=(snapshot.session_id, snapshot.symbol, snapshot.timeframe),
    )


def follows(candles: tuple[Candle, ...], candle: Candle) -> bool:
    """Whether this candle comes after every candle in an ascending tuple."""
    return not candles or candle.open_time > candles[-1].open_time


def _session_identity(
    session: ConfiguredPaperSession | None,
    session_facts: tuple[UUID, str, str] | None,
) -> tuple[UUID, str, str]:
    """Which Session a snapshot is of, from whichever source names it.

    A snapshot is normally built from the Session itself. The facts are the
    way a derived snapshot carries the same identity forward without holding
    the whole configuration — but only one of the two may answer, because two
    answers is a snapshot that could be of two different Sessions.
    """
    if session is not None and session_facts is not None:
        raise ValueError("ChartSnapshot accepts one Session source")
    if session is not None:
        return (
            session.config.session_id,
            session.market_data.symbol,
            session.market_data.timeframe,
        )
    if session_facts is not None:
        return session_facts
    raise ValueError("ChartSnapshot requires a ConfiguredPaperSession")


def _require_session_facts(*, symbol: str, timeframe: str) -> None:
    if not symbol:
        raise ValueError("Session symbol must not be empty")
    if not timeframe:
        raise ValueError("Session timeframe must not be empty")


def _require_state_matches_data(snapshot: "ChartSnapshot") -> None:
    """What a snapshot is allowed to carry, given what it says it is.

    A snapshot that is still loading or could not be read has nothing to
    show, so carrying candles would let a screen draw data while telling the
    reader there is none.
    """
    _require_utc(snapshot.observed_at_utc, "observed_at_utc")
    if snapshot.chart_range.end > snapshot.observed_at_utc:
        raise ValueError("ChartRange end must not be after observed_at_utc")
    if not isinstance(snapshot.state, ChartReadState):
        raise ValueError("state must be a ChartReadState")
    if (
        snapshot.message is not None
        and snapshot.state is not ChartReadState.UNAVAILABLE
    ):
        raise ValueError("message is only valid for unavailable ChartSnapshot")
    if snapshot.state is ChartReadState.UNAVAILABLE and not snapshot.message:
        raise ValueError("unavailable ChartSnapshot requires a message")
    if snapshot.state in {ChartReadState.LOADING, ChartReadState.UNAVAILABLE} and (
        snapshot.candles or snapshot.fills
    ):
        raise ValueError("loading or unavailable ChartSnapshot cannot contain data")
    if snapshot.state is ChartReadState.EMPTY and snapshot.candles:
        raise ValueError("empty ChartSnapshot cannot contain candles")


def _require_in_progress_candle(snapshot: "ChartSnapshot") -> None:
    """The forming candle, which sits past the completed range, not inside it.

    It is the one candle the snapshot holds that is still being observed, so
    it has to be the next one after the range and the moment of observation
    has to fall within it — otherwise it is a completed candle in the wrong
    place, or a future one nothing has seen yet.
    """
    candle = snapshot.in_progress_candle
    if candle is None:
        return
    if snapshot.state not in {ChartReadState.READY, ChartReadState.EMPTY}:
        raise ValueError("in_progress_candle requires a ready or empty ChartSnapshot")
    if not isinstance(candle, Candle):
        raise ValueError("in_progress_candle must be a Candle")
    if candle.symbol != snapshot.symbol:
        raise ValueError("in_progress_candle symbol must match Session")
    if candle.timeframe != snapshot.timeframe:
        raise ValueError("in_progress_candle timeframe must match Session")
    if candle.open_time != snapshot.chart_range.end:
        raise ValueError("in_progress_candle open_time must match ChartRange end")
    if not candle.open_time <= snapshot.observed_at_utc < candle.close_time:
        raise ValueError("in_progress_candle must be observed inside its interval")


def _require_completed_candles(snapshot: "ChartSnapshot") -> None:
    """Candles of this Session, inside this range, in the order they closed.

    Ascending unique open times are what let the chart draw by position
    rather than by searching: a repeated or out-of-order candle would be
    drawn where a different minute belongs.
    """
    previous_open_time: datetime | None = None
    for candle in snapshot.candles:
        if not isinstance(candle, Candle):
            raise ValueError("candles must be Candle values")
        if candle.symbol != snapshot.symbol:
            raise ValueError("candle symbol must match Session")
        if candle.timeframe != snapshot.timeframe:
            raise ValueError("candle timeframe must match Session")
        if candle.close_time > snapshot.observed_at_utc:
            raise ValueError("candle must be completed by observed_at_utc")
        if (
            candle.open_time < snapshot.chart_range.start
            or candle.close_time > snapshot.chart_range.end
        ):
            raise ValueError("candle must be completed inside ChartRange")
        if previous_open_time is not None and candle.open_time <= previous_open_time:
            raise ValueError("candles must have ascending unique open_time values")
        previous_open_time = candle.open_time


def _markers_of(snapshot: "ChartSnapshot") -> tuple[ChartMarker, ...]:
    """The marks the fills leave on the chart, and the reading that makes them.

    Which Session took a fill decides nothing here: a trade of this Pair is a
    trade of this Pair, and refusing the ones a previous Session made emptied
    the chart every time a Start replaced one. The Pair is the query's to
    guarantee — a TradeFill carries no symbol to check it by — and what is
    still readable from a fill alone is checked: one outside the span being
    drawn, or a duplicate, is one there is no mark to make for, so it is
    rejected where it would have been drawn.
    """
    markers: list[ChartMarker] = []
    fill_ids: set[str] = set()
    for fill in snapshot.fills:
        if not snapshot.visible_range.contains(fill.filled_at_utc):
            raise ValueError("fill must be inside the visible range")
        if fill.fill_id in fill_ids:
            raise ValueError("fills must have unique fill_id values")
        fill_ids.add(fill.fill_id)
        markers.append(ChartMarker.from_fill(fill, snapshot.timeframe))
    return tuple(markers)


def _require_indicators(snapshot: "ChartSnapshot") -> None:
    """Indicators only where there is a candle they were read off.

    An indicator value at a time the snapshot has no candle for is a reading
    of something not on the chart, which the chart would draw anyway.
    """
    if snapshot.state in {ChartReadState.LOADING, ChartReadState.UNAVAILABLE} and (
        snapshot.rsi.values
        or snapshot.rsi.state is not None
        or snapshot.atr.values
        or snapshot.atr.state is not None
    ):
        raise ValueError("loading or unavailable ChartSnapshot cannot carry indicators")
    candle_open_times = {candle.open_time for candle in snapshot.candles}
    if not candle_open_times.issuperset(snapshot.rsi.values):
        raise ValueError("RSI open_time must match a ChartSnapshot candle")
    if not candle_open_times.issuperset(snapshot.atr.values):
        raise ValueError("ATR open_time must match a ChartSnapshot candle")
