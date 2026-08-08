from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
    latest_completed_boundary,
)
from tiewtrade.trading.trade_history import FillSide
from tiewtrade.ui.chart_painter_model import (
    AxisTickGlyph,
    CrosshairGlyph,
    build_painter_model,
    crosshair_at,
)


class CandlestickChartWidget(QWidget):
    """Read-only QPainter surface for a validated chart snapshot."""

    range_requested = Signal(object)
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("candlestickChart")
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self._snapshot: ChartSnapshot | None = None
        self._crosshair_position: QPointF | None = None

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

    @property
    def crosshair(self) -> CrosshairGlyph | None:
        snapshot = self._snapshot
        position = self._crosshair_position
        if snapshot is None or position is None or not snapshot.display_candles:
            return None
        model = build_painter_model(snapshot, _chart_surface(self))
        return crosshair_at(position, model)

    def show_snapshot(self, snapshot: ChartSnapshot) -> None:
        self._snapshot = snapshot
        self._crosshair_position = None
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
        surface = _chart_surface(self)
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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._crosshair_position = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._crosshair_position = None
        self.update()
        super().leaveEvent(event)

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
        model = build_painter_model(snapshot, surface)
        painter.setPen(QPen(QColor("#26313F"), 1))
        for line in model.grid_lines:
            painter.drawLine(line)
        painter.setPen(QPen(QColor("#94A3B8"), 1))
        for tick in model.price_ticks + model.time_ticks:
            painter.drawLine(tick.line)
            painter.drawText(tick.label_rect, 0x84, tick.label)
        for candle in model.candles:
            painter.setPen(QPen(candle.color, 1))
            painter.drawLine(candle.wick)
            painter.fillRect(candle.body, candle.color)
        for marker in model.markers:
            painter.setPen(QPen(marker.color, 2))
            painter.setBrush(marker.color)
            painter.drawPolygon(QPolygonF(marker.triangle))
            painter.drawText(marker.label_rect, 0x84, marker.label)
        current_price = model.current_price
        if current_price is not None:
            painter.setPen(QPen(current_price.color, 1, Qt.PenStyle.DotLine))
            painter.drawLine(current_price.line)
            painter.fillRect(current_price.label_rect, current_price.color)
            painter.setPen(QPen(QColor("#F8FAFC"), 1))
            painter.drawText(
                current_price.label_rect,
                0x84,
                f"{current_price.price_text}\n{current_price.remaining_text}",
            )
        crosshair = self.crosshair
        if crosshair is None:
            return
        painter.setPen(QPen(QColor("#64748B"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(crosshair.vertical)
        painter.drawLine(crosshair.horizontal)
        self._draw_crosshair_label(painter, crosshair.price_label)
        self._draw_crosshair_label(painter, crosshair.time_label)

    @staticmethod
    def _draw_crosshair_label(painter: QPainter, label: AxisTickGlyph) -> None:
        painter.fillRect(label.label_rect, QColor("#334155"))
        painter.setPen(QPen(QColor("#F8FAFC"), 1))
        painter.drawText(label.label_rect, 0x84, label.label)


def _chart_surface(widget: QWidget) -> QRectF:
    return QRectF(12, 54, max(1, widget.width() - 24), max(1, widget.height() - 66))


def _range_text(chart_range: ChartRange) -> str:
    return f"{chart_range.start:%Y-%m-%d %H:%M}–{chart_range.end:%H:%M} UTC"
