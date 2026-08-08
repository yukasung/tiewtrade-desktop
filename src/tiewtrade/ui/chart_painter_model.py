from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import QLineF, QPointF, QRectF
from PySide6.QtGui import QColor

from tiewtrade.application.chart_data import ChartRange, ChartSnapshot
from tiewtrade.market_data.config import timeframe_to_interval
from tiewtrade.trading.trade_history import FillSide


@dataclass(frozen=True, slots=True)
class CandleGlyph:
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
    price_surface = _plot_surface(surface)
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
            22.0,
        )
        y = _price_y(marker.price, low, high, price_surface)
        is_buy = marker.side is FillSide.BUY
        color = QColor("#0ECB81" if is_buy else "#F6465D")
        triangle = (
            QPointF(x, y - 8 if is_buy else y + 8),
            QPointF(x - 6, y + 2 if is_buy else y - 2),
            QPointF(x + 6, y + 2 if is_buy else y - 2),
        )
        label_height = 16.0
        preferred_label_top = y - 28 if is_buy else y + 10
        alternate_label_top = y + 10 if is_buy else y - 28
        label_top = preferred_label_top
        if not (
            price_surface.top() <= label_top
            and label_top + label_height <= price_surface.bottom()
        ):
            label_top = alternate_label_top
        label_top = min(
            max(label_top, price_surface.top()),
            price_surface.bottom() - label_height,
        )
        markers.append(
            MarkerGlyph(
                triangle=triangle,
                label_rect=QRectF(
                    _clamp_x(x, price_surface, 22.0) - 22,
                    label_top,
                    44,
                    label_height,
                ),
                label="Buy" if is_buy else "Sell",
                color=color,
            )
        )
    current_price = _current_price_glyph(snapshot, low, high, price_surface, surface)
    return ChartPainterModel(
        plot_surface=price_surface,
        price_low=low,
        price_high=high,
        visual_range=visual_range,
        grid_lines=grid_lines,
        price_ticks=_price_ticks(low, high, price_surface, surface),
        time_ticks=_time_ticks(visual_range, price_surface, surface),
        candles=tuple(candles),
        markers=tuple(markers),
        current_price=current_price,
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


def _plot_surface(surface: QRectF) -> QRectF:
    return QRectF(
        surface.left(),
        surface.top(),
        max(1.0, surface.width() - 72),
        max(1.0, surface.height() - 26),
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
    axis_left = plot_surface.right() + 6
    axis_width = max(1.0, surface.right() - axis_left)
    for index in range(5):
        ratio = Decimal(index) / Decimal("4")
        value = low + (high - low) * ratio
        y = _price_y(value, low, high, plot_surface)
        labels.append(
            AxisTickGlyph(
                line=QLineF(plot_surface.right(), y, plot_surface.right() + 4, y),
                label_rect=QRectF(axis_left, y - 9, axis_width, 18),
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
    if not plot.contains(position):
        return None
    price = _price_at(position.y(), model.price_low, model.price_high, plot)
    ratio = (position.x() - plot.left()) / plot.width()
    timestamp = (
        model.visual_range.start
        + (model.visual_range.end - model.visual_range.start) * ratio
    )
    price_axis = model.price_ticks[0].label_rect
    label_height = price_axis.height()
    price_label_top = min(
        max(position.y() - label_height / 2, plot.top()),
        plot.bottom() - label_height,
    )
    time_axis = model.time_ticks[0].label_rect
    time_label_width = min(144.0, plot.width())
    time_label_left = min(
        max(position.x() - time_label_width / 2, plot.left()),
        plot.right() - time_label_width,
    )
    return CrosshairGlyph(
        vertical=QLineF(position.x(), plot.top(), position.x(), plot.bottom()),
        horizontal=QLineF(plot.left(), position.y(), plot.right(), position.y()),
        price_label=AxisTickGlyph(
            line=QLineF(),
            label_rect=QRectF(
                price_axis.left(),
                price_label_top,
                price_axis.width(),
                label_height,
            ),
            label=_price_text(price),
            value=str(price),
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
