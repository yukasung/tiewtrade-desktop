from PySide6.QtCore import QEvent, QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
    latest_completed_boundary,
    zoomed_chart_range,
)
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_symbols import PAPER_SYMBOLS
from tiewtrade.market_data.config import SUPPORTED_V1_TIMEFRAME_CHOICES
from tiewtrade.ui.chart_painter_model import (
    CHART_SURFACE_COLOR,
    MARKER_BADGE_RADIUS,
    MARKER_RING_WIDTH,
    AxisTickGlyph,
    CandleReadout,
    ChartPainterModel,
    CrosshairGlyph,
    build_painter_model,
    candle_at,
    crosshair_at,
    marker_label,
)

_ALIGN_LEFT_TOP = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)


class CandlestickChartWidget(QWidget):
    """Read-only QPainter surface for a validated chart snapshot."""

    range_requested = Signal(object)
    retry_requested = Signal()
    view_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("candlestickChart")
        self.setMinimumHeight(360)
        self.setMouseTracking(True)
        self._snapshot: ChartSnapshot | None = None
        self._crosshair_position: QPointF | None = None

        self._header = QHBoxLayout()
        self._header.setContentsMargins(12, 10, 12, 0)
        self._header.setSpacing(0)
        self._facts = QLabel("Chart")
        self._facts.setObjectName("chartFacts")
        self.symbol = QComboBox()
        self.symbol.setObjectName("chartSymbol")
        self.symbol.setAccessibleName("Chart Symbol")
        for symbol in PAPER_SYMBOLS:
            self.symbol.addItem(symbol, symbol)
        self.timeframe = QComboBox()
        self.timeframe.setObjectName("chartTimeframe")
        self.timeframe.setAccessibleName("Chart Timeframe")
        for timeframe in SUPPORTED_V1_TIMEFRAME_CHOICES:
            self.timeframe.addItem(timeframe, timeframe)
        self.timeframe.setCurrentIndex(self.timeframe.findData("5m"))
        self._range = QLabel()
        self._range.setObjectName("chartRange")
        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addWidget(self._range)
        navigation = QHBoxLayout()
        navigation.setSpacing(8)
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setObjectName("zoomOutButton")
        self.zoom_out_button.setFixedWidth(44)
        self.zoom_out_button.setAccessibleName("Zoom out chart")
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setObjectName("zoomInButton")
        self.zoom_in_button.setFixedWidth(44)
        self.zoom_in_button.setAccessibleName("Zoom in chart")
        self.previous_range_button = QPushButton("←")
        self.previous_range_button.setObjectName("previousRangeButton")
        self.previous_range_button.setFixedWidth(44)
        self.previous_range_button.setAccessibleName("Previous chart range")
        self.next_range_button = QPushButton("→")
        self.next_range_button.setObjectName("nextRangeButton")
        self.next_range_button.setFixedWidth(44)
        self.next_range_button.setAccessibleName("Next chart range")
        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("chartRetryButton")
        self.retry_button.setAccessibleName("Retry chart")
        self.retry_button.hide()
        self.previous_range_button.clicked.connect(self._request_previous_range)
        self.next_range_button.clicked.connect(self._request_next_range)
        self.zoom_out_button.clicked.connect(self._request_zoom_out)
        self.zoom_in_button.clicked.connect(self._request_zoom_in)
        self.retry_button.clicked.connect(self.retry_requested)
        self.symbol.currentIndexChanged.connect(self._request_view)
        self.timeframe.currentIndexChanged.connect(self._request_view)
        navigation.addWidget(self.previous_range_button)
        navigation.addWidget(self.next_range_button)
        navigation.addWidget(self.zoom_out_button)
        navigation.addWidget(self.zoom_in_button)
        controls.addLayout(navigation)
        controls.addWidget(self.retry_button)
        self._header.addWidget(self.symbol)
        self._header.addSpacing(12)
        self._header.addWidget(self.timeframe)
        self._header.addSpacing(16)
        self._header.addWidget(self._facts)
        self._header.addStretch()
        self._header.addLayout(controls)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._header)
        layout.addStretch()

    @property
    def marker_labels(self) -> tuple[str, ...]:
        if self._snapshot is None:
            return ()
        return tuple(marker_label(marker.side) for marker in self._snapshot.markers)

    @property
    def crosshair(self) -> CrosshairGlyph | None:
        model = self._painter_model()
        if model is None:
            return None
        return self._crosshair_for(model)

    @property
    def readout(self) -> CandleReadout | None:
        """Values shown in the legend: the hovered candle, else the latest."""
        model = self._painter_model()
        if model is None:
            return None
        return self._readout_for(model)

    def _crosshair_for(self, model: ChartPainterModel) -> CrosshairGlyph | None:
        position = self._crosshair_position
        if position is None:
            return None
        return crosshair_at(position, model)

    def _readout_for(self, model: ChartPainterModel) -> CandleReadout | None:
        if self._snapshot is None:
            return None
        return candle_at(self._crosshair_position, self._snapshot, model)

    def _painter_model(self) -> ChartPainterModel | None:
        """Build a model outside a paint. Painting reuses the one it built."""
        snapshot = self._snapshot
        if snapshot is None or not snapshot.display_candles:
            return None
        return build_painter_model(snapshot, self._chart_surface())

    def show_snapshot(self, snapshot: ChartSnapshot) -> None:
        self._snapshot = snapshot
        self._crosshair_position = None
        self.setAccessibleName(
            f"Candlestick chart for {snapshot.symbol} {snapshot.timeframe}"
        )
        self._facts.setText(f"{snapshot.symbol} · {snapshot.timeframe} · Paper")
        with QSignalBlocker(self.symbol), QSignalBlocker(self.timeframe):
            self.symbol.setCurrentIndex(self.symbol.findData(snapshot.symbol))
            self.timeframe.setCurrentIndex(self.timeframe.findData(snapshot.timeframe))
        self._range.setText(_range_text(snapshot.chart_range))
        has_range = snapshot.state in {ChartReadState.READY, ChartReadState.EMPTY}
        self.zoom_in_button.setEnabled(
            has_range
            and zoomed_chart_range(
                snapshot.chart_range, snapshot.timeframe, zoom_in=True
            )
            != snapshot.chart_range
        )
        self.zoom_out_button.setEnabled(
            has_range
            and zoomed_chart_range(
                snapshot.chart_range, snapshot.timeframe, zoom_in=False
            )
            != snapshot.chart_range
        )
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

    def set_view_loading(self, loading: bool) -> None:
        self.symbol.setDisabled(loading)
        self.timeframe.setDisabled(loading)
        self.zoom_in_button.setDisabled(loading)
        self.zoom_out_button.setDisabled(loading)

    def _request_view(self) -> None:
        symbol = self.symbol.currentData()
        timeframe = self.timeframe.currentData()
        if not isinstance(symbol, str) or not isinstance(timeframe, str):
            return
        self.view_requested.emit(ChartViewSelection(symbol, timeframe))

    def paintEvent(self, event: object) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(CHART_SURFACE_COLOR))
        snapshot = self._snapshot
        if snapshot is None:
            painter.end()
            return
        surface = self._chart_surface()
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

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._request_zoom(zoom_in=event.angleDelta().y() > 0):
            event.accept()
            return
        super().wheelEvent(event)

    def _request_zoom_in(self) -> None:
        self._request_zoom(zoom_in=True)

    def _request_zoom_out(self) -> None:
        self._request_zoom(zoom_in=False)

    def _request_zoom(self, *, zoom_in: bool) -> bool:
        """Emit a narrower or wider range; report whether anything changed."""
        snapshot = self._snapshot
        if snapshot is None or snapshot.state not in {
            ChartReadState.READY,
            ChartReadState.EMPTY,
        }:
            return False
        zoomed = zoomed_chart_range(
            snapshot.chart_range,
            snapshot.timeframe,
            zoom_in=zoom_in,
        )
        if zoomed == snapshot.chart_range:
            return False
        self.range_requested.emit(zoomed)
        return True

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
            painter.setPen(QPen(marker.color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(marker.leader)
            painter.setPen(QPen(QColor(CHART_SURFACE_COLOR), MARKER_RING_WIDTH))
            painter.setBrush(marker.color)
            painter.drawRoundedRect(
                marker.badge,
                MARKER_BADGE_RADIUS,
                MARKER_BADGE_RADIUS,
            )
            painter.setPen(QPen(QColor("#0B1118"), 1))
            painter.drawText(marker.badge, 0x84, marker.label)
        painter.setBrush(Qt.BrushStyle.NoBrush)
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
        self._draw_rsi_pane(painter, model)
        crosshair = self._crosshair_for(model)
        if crosshair is not None:
            painter.setPen(QPen(QColor("#64748B"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(crosshair.vertical)
            painter.drawLine(crosshair.horizontal)
            self._draw_crosshair_label(painter, crosshair.price_label)
            self._draw_crosshair_label(painter, crosshair.time_label)
        self._draw_legend(painter, model)

    def _draw_rsi_pane(self, painter: QPainter, model: ChartPainterModel) -> None:
        pane = model.rsi_pane
        painter.setPen(QPen(QColor("#26313F"), 1, Qt.PenStyle.DashLine))
        for guide in pane.guides:
            painter.drawLine(guide.line)
        painter.setPen(QPen(QColor("#94A3B8"), 1))
        for guide in pane.guides:
            painter.drawText(guide.label_rect, 0x84, guide.label)
        painter.setPen(QPen(pane.color, 1))
        for polyline in pane.polylines:
            painter.drawPolyline(QPolygonF(polyline))

    def _draw_legend(self, painter: QPainter, model: ChartPainterModel) -> None:
        readout = self._readout_for(model)
        if readout is None:
            return
        plot = model.plot_surface
        rect = QRectF(plot.left() + 8, plot.top() + 4, max(1.0, plot.width() - 16), 34)
        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.drawText(rect, _ALIGN_LEFT_TOP, _legend_text(readout))

    def _chart_surface(self) -> QRectF:
        top = self._header.geometry().bottom() + 24
        return QRectF(
            12,
            top,
            max(1, self.width() - 24),
            max(1, self.height() - top - 12),
        )

    @staticmethod
    def _draw_crosshair_label(painter: QPainter, label: AxisTickGlyph) -> None:
        painter.fillRect(label.label_rect, QColor("#334155"))
        painter.setPen(QPen(QColor("#F8FAFC"), 1))
        painter.drawText(label.label_rect, 0x84, label.label)


def _range_text(chart_range: ChartRange) -> str:
    return f"{chart_range.start:%Y-%m-%d %H:%M}–{chart_range.end:%H:%M} UTC"


def _legend_text(readout: CandleReadout) -> str:
    candle = readout.candle
    rsi = "—" if readout.rsi is None else f"{readout.rsi:,.2f}"
    forming = "  · forming" if readout.is_in_progress else ""
    return (
        f"{candle.open_time:%Y-%m-%d %H:%M} UTC{forming}\n"
        f"O {candle.open:,.2f}  H {candle.high:,.2f}  "
        f"L {candle.low:,.2f}  C {candle.close:,.2f}  "
        f"RSI({readout.rsi_period}) {rsi}"
    )
