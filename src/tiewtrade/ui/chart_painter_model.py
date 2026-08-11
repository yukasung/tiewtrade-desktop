from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import QLineF, QPointF, QRectF
from PySide6.QtGui import QColor

from tiewtrade.application.chart_data import ChartCandle, ChartRange, ChartSnapshot
from tiewtrade.market_data.config import timeframe_to_interval
from tiewtrade.trading.trade_history import FillSide

_PANE_GAP = 10.0
_RSI_PANE_RATIO = 0.26
_RSI_PANE_MINIMUM_HEIGHT = 56.0
_RSI_GUIDE_LEVELS = (30, 50, 70)
_RSI_LINE_COLOR = "#F0B90B"
# The RSI pane is a fixed 0-100 scale, so it reuses the price projection
# rather than carrying its own value-to-pixel mapping.
_RSI_LOW = Decimal("0")
_RSI_HIGH = Decimal("100")
_AXIS_GUTTER = 6.0
_AXIS_LABEL_HEIGHT = 18.0
# Fill markers are single-letter badges so a run of entries stays readable
# when candles are packed together.
_MARKER_BADGE_SIZE = 18.0
MARKER_BADGE_RADIUS = 5.0


def marker_label(side: FillSide) -> str:
    return "B" if side is FillSide.BUY else "S"


@dataclass(frozen=True, slots=True)
class CandleGlyph:
    open_time: datetime
    x: float
    wick: QLineF
    body: QRectF
    color: QColor


@dataclass(frozen=True, slots=True)
class MarkerGlyph:
    triangle: tuple[QPointF, QPointF, QPointF]
    label_rect: QRectF
    label: str
    color: QColor


@dataclass(frozen=True, slots=True)
class AxisTickGlyph:
    line: QLineF
    label_rect: QRectF
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class CrosshairGlyph:
    vertical: QLineF
    horizontal: QLineF
    price_label: AxisTickGlyph
    time_label: AxisTickGlyph


@dataclass(frozen=True, slots=True)
class CurrentPriceGlyph:
    line: QLineF
    label_rect: QRectF
    price_text: str
    remaining_text: str
    color: QColor


@dataclass(frozen=True, slots=True)
class RsiPaneGlyph:
    surface: QRectF
    # One entry per unbroken run of candles that have an RSI value, so the
    # warm-up gap is never bridged by a line that was not measured.
    polylines: tuple[tuple[QPointF, ...], ...]
    guides: tuple[AxisTickGlyph, ...]
    color: QColor


@dataclass(frozen=True, slots=True)
class CandleReadout:
    candle: ChartCandle
    rsi: Decimal | None
    rsi_period: int
    is_in_progress: bool


@dataclass(frozen=True, slots=True)
class ChartPainterModel:
    plot_surface: QRectF
    price_low: Decimal
    price_high: Decimal
    visual_range: ChartRange
    grid_lines: tuple[QLineF, ...]
    price_ticks: tuple[AxisTickGlyph, ...]
    time_ticks: tuple[AxisTickGlyph, ...]
    candles: tuple[CandleGlyph, ...]
    markers: tuple[MarkerGlyph, ...]
    current_price: CurrentPriceGlyph | None
    rsi_pane: RsiPaneGlyph


def build_painter_model(
    snapshot: ChartSnapshot,
    surface: QRectF,
) -> ChartPainterModel:
    display_candles = snapshot.display_candles
    visual_range = snapshot.chart_range
    if snapshot.in_progress_candle is not None:
        visual_range = ChartRange(
            start=snapshot.chart_range.start,
            end=snapshot.chart_range.end + timeframe_to_interval(snapshot.timeframe),
        )
    prices = [
        price for candle in display_candles for price in (candle.high, candle.low)
    ] + [marker.price for marker in snapshot.markers]
    low, high = _padded_price_range(min(prices), max(prices))
    price_surface, rsi_surface = _panes(surface)
    grid_lines = tuple(
        QLineF(
            price_surface.left(),
            price_surface.top() + price_surface.height() * line / 4,
            price_surface.right(),
            price_surface.top() + price_surface.height() * line / 4,
        )
        for line in range(5)
    )
    width = min(
        max(4.0, price_surface.width() / max(len(display_candles) * 2, 2)),
        price_surface.width(),
    )
    candles: list[CandleGlyph] = []
    for candle in display_candles:
        x = _clamp_x(
            _marker_x(candle.open_time, visual_range, price_surface),
            price_surface,
            width / 2,
        )
        color = QColor("#0ECB81" if candle.close >= candle.open else "#F6465D")
        body_top = _price_y(max(candle.open, candle.close), low, high, price_surface)
        body_bottom = _price_y(min(candle.open, candle.close), low, high, price_surface)
        candles.append(
            CandleGlyph(
                open_time=candle.open_time,
                x=x,
                wick=QLineF(
                    x,
                    _price_y(candle.high, low, high, price_surface),
                    x,
                    _price_y(candle.low, low, high, price_surface),
                ),
                body=QRectF(
                    x - width / 2,
                    body_top,
                    width,
                    max(1.0, body_bottom - body_top),
                ),
                color=color,
            )
        )
    markers: list[MarkerGlyph] = []
    for marker in snapshot.markers:
        x = _clamp_x(
            _marker_x(marker.filled_at_utc, snapshot.chart_range, price_surface),
            price_surface,
            _MARKER_BADGE_SIZE / 2,
        )
        y = _price_y(marker.price, low, high, price_surface)
        is_buy = marker.side is FillSide.BUY
        color = QColor("#0ECB81" if is_buy else "#F6465D")
        triangle = (
            QPointF(x, y - 8 if is_buy else y + 8),
            QPointF(x - 6, y + 2 if is_buy else y - 2),
            QPointF(x + 6, y + 2 if is_buy else y - 2),
        )
        # The badge sits beyond the triangle's tip, flipping to the other side
        # when that would push it out of the pane.
        preferred_top = y - 30 if is_buy else y + 12
        alternate_top = y + 12 if is_buy else y - 30
        badge_top = preferred_top
        if not (
            price_surface.top() <= badge_top
            and badge_top + _MARKER_BADGE_SIZE <= price_surface.bottom()
        ):
            badge_top = alternate_top
        badge_top = min(
            max(badge_top, price_surface.top()),
            price_surface.bottom() - _MARKER_BADGE_SIZE,
        )
        markers.append(
            MarkerGlyph(
                triangle=triangle,
                label_rect=QRectF(
                    x - _MARKER_BADGE_SIZE / 2,
                    badge_top,
                    _MARKER_BADGE_SIZE,
                    _MARKER_BADGE_SIZE,
                ),
                label=marker_label(marker.side),
                color=color,
            )
        )
    current_price = _current_price_glyph(snapshot, low, high, price_surface, surface)
    # Read once per build: the forming candle's RSI is derived on each call.
    rsi_values = [snapshot.rsi_for(candle.open_time) for candle in display_candles]
    return ChartPainterModel(
        plot_surface=price_surface,
        price_low=low,
        price_high=high,
        visual_range=visual_range,
        grid_lines=grid_lines,
        price_ticks=_price_ticks(low, high, price_surface, surface),
        time_ticks=_time_ticks(visual_range, rsi_surface, surface),
        candles=tuple(candles),
        markers=tuple(markers),
        current_price=current_price,
        rsi_pane=_rsi_pane_glyph(rsi_values, candles, rsi_surface, surface),
    )


def _rsi_pane_glyph(
    rsi_values: Sequence[Decimal | None],
    candles: Sequence[CandleGlyph],
    rsi_surface: QRectF,
    surface: QRectF,
) -> RsiPaneGlyph:
    guides: list[AxisTickGlyph] = []
    for level in _RSI_GUIDE_LEVELS:
        y = _rsi_y(Decimal(level), rsi_surface)
        guides.append(
            AxisTickGlyph(
                line=QLineF(rsi_surface.left(), y, rsi_surface.right(), y),
                label_rect=_axis_label_rect(y, rsi_surface, surface),
                label=str(level),
                value=str(level),
            )
        )
    runs: list[list[QPointF]] = [[]]
    for value, glyph in zip(rsi_values, candles, strict=True):
        if value is None:
            runs.append([])
            continue
        runs[-1].append(QPointF(glyph.x, _rsi_y(value, rsi_surface)))
    return RsiPaneGlyph(
        surface=rsi_surface,
        # A single point cannot be drawn as a line, so runs shorter than two
        # points are dropped rather than bridged across the warm-up gap.
        polylines=tuple(tuple(run) for run in runs if len(run) > 1),
        guides=tuple(guides),
        color=QColor(_RSI_LINE_COLOR),
    )


def candle_at(
    position: QPointF | None,
    snapshot: ChartSnapshot,
    model: ChartPainterModel,
) -> CandleReadout | None:
    """Values of the candle nearest the cursor, else of the latest candle."""
    display_candles = snapshot.display_candles
    if not display_candles:
        return None
    if position is None or not _inside_panes(position, model):
        candle = display_candles[-1]
    else:
        nearest = min(model.candles, key=lambda glyph: abs(glyph.x - position.x()))
        candle = next(
            item for item in display_candles if item.open_time == nearest.open_time
        )
    forming = snapshot.in_progress_candle
    return CandleReadout(
        candle=candle,
        rsi=snapshot.rsi_for(candle.open_time),
        rsi_period=snapshot.rsi.period,
        is_in_progress=forming is not None and forming.open_time == candle.open_time,
    )


def _inside_panes(position: QPointF, model: ChartPainterModel) -> bool:
    return model.plot_surface.contains(position) or model.rsi_pane.surface.contains(
        position
    )


def _current_price_glyph(
    snapshot: ChartSnapshot,
    low: Decimal,
    high: Decimal,
    plot_surface: QRectF,
    surface: QRectF,
) -> CurrentPriceGlyph | None:
    candle = snapshot.in_progress_candle
    if candle is None:
        return None
    y = _price_y(candle.close, low, high, plot_surface)
    if not plot_surface.top() <= y <= plot_surface.bottom():
        return None
    color = QColor("#0ECB81" if candle.close >= candle.open else "#F6465D")
    label_left = plot_surface.right() + 6
    label_rect = QRectF(label_left, y - 21, surface.right() - label_left, 42)
    remaining = max(candle.close_time - snapshot.observed_at_utc, timedelta())
    return CurrentPriceGlyph(
        line=QLineF(plot_surface.left(), y, plot_surface.right(), y),
        label_rect=label_rect,
        price_text=_price_text(candle.close),
        remaining_text=_remaining_time_text(remaining),
        color=color,
    )


def _panes(surface: QRectF) -> tuple[QRectF, QRectF]:
    """Split the area left by the price and time axes into price and RSI panes.

    Both rects come from one height calculation so they always abut.
    """
    body = QRectF(
        surface.left(),
        surface.top(),
        max(1.0, surface.width() - 72),
        max(1.0, surface.height() - 26),
    )
    available = max(0.0, body.height() - _PANE_GAP)
    rsi_height = max(
        1.0,
        min(available, max(_RSI_PANE_MINIMUM_HEIGHT, available * _RSI_PANE_RATIO)),
    )
    price_height = max(1.0, body.height() - rsi_height - _PANE_GAP)
    return (
        QRectF(body.left(), body.top(), body.width(), price_height),
        QRectF(body.left(), body.bottom() - rsi_height, body.width(), rsi_height),
    )


def _rsi_y(value: Decimal, surface: QRectF) -> float:
    return _price_y(value, _RSI_LOW, _RSI_HIGH, surface)


def _rsi_at(y: float, surface: QRectF) -> Decimal:
    return _price_at(y, _RSI_LOW, _RSI_HIGH, surface)


def _axis_label_rect(y: float, pane: QRectF, surface: QRectF) -> QRectF:
    """Label slot in the right-hand axis gutter, vertically centred on y."""
    left = pane.right() + _AXIS_GUTTER
    return QRectF(
        left,
        y - _AXIS_LABEL_HEIGHT / 2,
        max(1.0, surface.right() - left),
        _AXIS_LABEL_HEIGHT,
    )


def _padded_price_range(low: Decimal, high: Decimal) -> tuple[Decimal, Decimal]:
    if low == high:
        low -= Decimal("1")
        high += Decimal("1")
    padding = (high - low) * Decimal("0.05")
    return low - padding, high + padding


def _price_ticks(
    low: Decimal,
    high: Decimal,
    plot_surface: QRectF,
    surface: QRectF,
) -> tuple[AxisTickGlyph, ...]:
    labels: list[AxisTickGlyph] = []
    for index in range(5):
        ratio = Decimal(index) / Decimal("4")
        value = low + (high - low) * ratio
        y = _price_y(value, low, high, plot_surface)
        labels.append(
            AxisTickGlyph(
                line=QLineF(plot_surface.right(), y, plot_surface.right() + 4, y),
                label_rect=_axis_label_rect(y, plot_surface, surface),
                label=_price_text(value),
                value=str(value),
            )
        )
    return tuple(reversed(labels))


def _time_ticks(
    chart_range: ChartRange,
    plot_surface: QRectF,
    surface: QRectF,
) -> tuple[AxisTickGlyph, ...]:
    labels: list[AxisTickGlyph] = []
    duration = chart_range.end - chart_range.start
    label_top = plot_surface.bottom() + 5
    label_width = 56.0
    tick_count = min(12, max(2, int(plot_surface.width() // (label_width * 1.5)) + 1))
    for index in range(tick_count):
        ratio = index / (tick_count - 1)
        value = chart_range.start + duration * ratio
        x = plot_surface.left() + plot_surface.width() * ratio
        label_left = min(
            max(x - label_width / 2, plot_surface.left()),
            plot_surface.right() - label_width,
        )
        labels.append(
            AxisTickGlyph(
                line=QLineF(x, plot_surface.bottom(), x, plot_surface.bottom() + 4),
                label_rect=QRectF(
                    label_left,
                    label_top,
                    label_width,
                    surface.bottom() - label_top,
                ),
                label=_time_text(value),
                value=value.isoformat(),
            )
        )
    return tuple(labels)


def crosshair_at(
    position: QPointF,
    model: ChartPainterModel,
) -> CrosshairGlyph | None:
    plot = model.plot_surface
    rsi_pane = model.rsi_pane.surface
    # The cursor reads price in the upper pane and RSI in the lower one, so
    # the right-axis label follows whichever pane it is over.
    if plot.contains(position):
        pane = plot
        axis_value = _price_at(position.y(), model.price_low, model.price_high, plot)
        axis_label = _price_text(axis_value)
    elif rsi_pane.contains(position):
        pane = rsi_pane
        axis_value = _rsi_at(position.y(), rsi_pane)
        axis_label = f"{axis_value:.2f}"
    else:
        return None
    ratio = (position.x() - plot.left()) / plot.width()
    timestamp = (
        model.visual_range.start
        + (model.visual_range.end - model.visual_range.start) * ratio
    )
    price_axis = model.price_ticks[0].label_rect
    label_height = price_axis.height()
    price_label_top = min(
        max(position.y() - label_height / 2, pane.top()),
        pane.bottom() - label_height,
    )
    time_axis = model.time_ticks[0].label_rect
    time_label_width = min(144.0, plot.width())
    time_label_left = min(
        max(position.x() - time_label_width / 2, plot.left()),
        plot.right() - time_label_width,
    )
    return CrosshairGlyph(
        vertical=QLineF(position.x(), plot.top(), position.x(), rsi_pane.bottom()),
        horizontal=QLineF(pane.left(), position.y(), pane.right(), position.y()),
        price_label=AxisTickGlyph(
            line=QLineF(),
            label_rect=QRectF(
                price_axis.left(),
                price_label_top,
                price_axis.width(),
                label_height,
            ),
            label=axis_label,
            value=str(axis_value),
        ),
        time_label=AxisTickGlyph(
            line=QLineF(),
            label_rect=QRectF(
                time_label_left,
                time_axis.top(),
                time_label_width,
                time_axis.height(),
            ),
            label=f"{timestamp:%Y-%m-%d %H:%M UTC}",
            value=timestamp.isoformat(),
        ),
    )


def _price_text(value: Decimal) -> str:
    return f"{value:,.2f}"


def _time_text(value: datetime) -> str:
    return f"{value:%H:%M}"


def _remaining_time_text(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    return f"{seconds // 60:02}:{seconds % 60:02}"


def _price_y(price: Decimal, low: Decimal, high: Decimal, surface: QRectF) -> float:
    return surface.bottom() - float((price - low) / (high - low)) * surface.height()


def _price_at(y: float, low: Decimal, high: Decimal, surface: QRectF) -> Decimal:
    return low + Decimal(str((surface.bottom() - y) / surface.height())) * (high - low)


def _marker_x(value: datetime, chart_range: ChartRange, surface: QRectF) -> float:
    elapsed = (value - chart_range.start).total_seconds()
    duration = (chart_range.end - chart_range.start).total_seconds()
    return surface.left() + surface.width() * elapsed / duration


def _clamp_x(value: float, surface: QRectF, margin: float) -> float:
    effective_margin = min(margin, surface.width() / 2)
    return min(
        max(value, surface.left() + effective_margin),
        surface.right() - effective_margin,
    )
