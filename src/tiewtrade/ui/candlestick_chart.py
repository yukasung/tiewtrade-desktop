from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QLineF, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
    latest_completed_boundary,
)
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
class ChartPainterModel:
    grid_lines: tuple[QLineF, ...]
    candles: tuple[CandleGlyph, ...]
    markers: tuple[MarkerGlyph, ...]


class CandlestickChartWidget(QWidget):
    """Read-only QPainter surface for a validated chart snapshot."""

    range_requested = Signal(object)
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("candlestickChart")
        self.setMinimumHeight(280)
        self._snapshot: ChartSnapshot | None = None

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 0)
        self._facts = QLabel("Chart")
        self._facts.setObjectName("chartFacts")
        self._range = QLabel()
        self._range.setObjectName("chartRange")
        self.previous_range_button = QPushButton("Previous")
        self.previous_range_button.setObjectName("previousRangeButton")
        self.previous_range_button.setAccessibleName("Previous chart range")
        self.next_range_button = QPushButton("Next")
        self.next_range_button.setObjectName("nextRangeButton")
        self.next_range_button.setAccessibleName("Next chart range")
        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("chartRetryButton")
        self.retry_button.setAccessibleName("Retry chart")
        self.retry_button.hide()
        self.previous_range_button.clicked.connect(self._request_previous_range)
        self.next_range_button.clicked.connect(self._request_next_range)
        self.retry_button.clicked.connect(self.retry_requested)
        header.addWidget(self._facts)
        header.addStretch()
        header.addWidget(self._range)
        header.addWidget(self.previous_range_button)
        header.addWidget(self.next_range_button)
        header.addWidget(self.retry_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addStretch()

    @property
    def marker_labels(self) -> tuple[str, ...]:
        if self._snapshot is None:
            return ()
        return tuple(
            "Buy" if marker.side is FillSide.BUY else "Sell"
            for marker in self._snapshot.markers
        )

    def show_snapshot(self, snapshot: ChartSnapshot) -> None:
        self._snapshot = snapshot
        self.setAccessibleName(
            f"Candlestick chart for {snapshot.symbol} {snapshot.timeframe}"
        )
        self._facts.setText(f"{snapshot.symbol} · {snapshot.timeframe} · Paper")
        self._range.setText(_range_text(snapshot.chart_range))
        has_range = snapshot.state in {ChartReadState.READY, ChartReadState.EMPTY}
        self.previous_range_button.setEnabled(has_range)
        self.next_range_button.setEnabled(
            has_range
            and snapshot.chart_range.end
            < latest_completed_boundary(
                snapshot.timeframe,
                snapshot.observed_at_utc,
            )
        )
        self.retry_button.setVisible(snapshot.state is ChartReadState.UNAVAILABLE)
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#10151C"))
        snapshot = self._snapshot
        if snapshot is None:
            painter.end()
            return
        surface = QRectF(12, 54, max(1, self.width() - 24), max(1, self.height() - 66))
        if snapshot.state is ChartReadState.UNAVAILABLE:
            self._draw_message(
                painter, surface, snapshot.message or "Chart is unavailable"
            )
        elif snapshot.state is ChartReadState.LOADING:
            self._draw_message(painter, surface, "Loading chart")
        elif not snapshot.display_candles:
            self._draw_message(painter, surface, "No completed candles")
        else:
            self._draw_chart(painter, surface, snapshot)
        painter.end()

    def _request_previous_range(self) -> None:
        if self._snapshot is None:
            return
        chart_range = self._snapshot.chart_range
        self.range_requested.emit(
            ChartRange(
                chart_range.start - (chart_range.end - chart_range.start),
                chart_range.start,
            )
        )

    def _request_next_range(self) -> None:
        if self._snapshot is None:
            return
        chart_range = self._snapshot.chart_range
        duration = chart_range.end - chart_range.start
        latest_end = latest_completed_boundary(
            self._snapshot.timeframe,
            self._snapshot.observed_at_utc,
        )
        next_end = min(chart_range.end + duration, latest_end)
        next_range = ChartRange(next_end - duration, next_end)
        if next_range == chart_range:
            return
        self.range_requested.emit(next_range)

    def _draw_message(self, painter: QPainter, surface: QRectF, message: str) -> None:
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(surface, 0x84, message)

    def _draw_chart(
        self, painter: QPainter, surface: QRectF, snapshot: ChartSnapshot
    ) -> None:
        model = _build_painter_model(snapshot, surface)
        painter.setPen(QPen(QColor("#26313F"), 1))
        for line in model.grid_lines:
            painter.drawLine(line)
        for candle in model.candles:
            painter.setPen(QPen(candle.color, 1))
            painter.drawLine(candle.wick)
            painter.fillRect(candle.body, candle.color)
        for marker in model.markers:
            painter.setPen(QPen(marker.color, 2))
            painter.setBrush(marker.color)
            painter.drawPolygon(QPolygonF(marker.triangle))
            painter.drawText(marker.label_rect, 0x84, marker.label)


def _build_painter_model(
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
    low, high = min(prices), max(prices)
    if low == high:
        low -= Decimal("1")
        high += Decimal("1")
    price_surface = surface
    grid_lines = tuple(
        QLineF(
            price_surface.left(),
            price_surface.top() + price_surface.height() * line / 4,
            price_surface.right(),
            price_surface.top() + price_surface.height() * line / 4,
        )
        for line in range(5)
    )
    width = max(4.0, surface.width() / max(len(display_candles) * 2, 2))
    candles: list[CandleGlyph] = []
    for candle in display_candles:
        x = _marker_x(candle.open_time, visual_range, surface)
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
        x = _marker_x(marker.filled_at_utc, snapshot.chart_range, surface)
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
            surface.top() <= label_top and label_top + label_height <= surface.bottom()
        ):
            label_top = alternate_label_top
        label_top = min(
            max(label_top, surface.top()),
            surface.bottom() - label_height,
        )
        markers.append(
            MarkerGlyph(
                triangle=triangle,
                label_rect=QRectF(x - 22, label_top, 44, label_height),
                label="Buy" if is_buy else "Sell",
                color=color,
            )
        )
    return ChartPainterModel(
        grid_lines=grid_lines,
        candles=tuple(candles),
        markers=tuple(markers),
    )


def _range_text(chart_range: ChartRange) -> str:
    return f"{chart_range.start:%Y-%m-%d %H:%M}–{chart_range.end:%H:%M} UTC"


def _price_y(price: Decimal, low: Decimal, high: Decimal, surface: QRectF) -> float:
    return surface.bottom() - float((price - low) / (high - low)) * surface.height()


def _marker_x(value: datetime, chart_range: ChartRange, surface: QRectF) -> float:
    elapsed = (value - chart_range.start).total_seconds()
    duration = (chart_range.end - chart_range.start).total_seconds()
    return surface.left() + surface.width() * elapsed / duration
