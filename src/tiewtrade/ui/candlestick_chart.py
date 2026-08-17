from PySide6.QtCore import QEvent, QPointF, QRectF, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
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
    MarkerGlyph,
    build_painter_model,
    candle_at,
    crosshair_at,
    marker_label,
)
from tiewtrade.ui.display_time import in_display_timezone

_ALIGN_LEFT_TOP = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
_ALIGN_CENTER = int(Qt.AlignmentFlag.AlignCenter)
# One palette for the painted layer; the themed widgets read theme.py instead.
_GRID_COLOR = "#26313F"
_AXIS_TEXT_COLOR = "#94A3B8"
_LEGEND_TEXT_COLOR = "#CBD5E1"
_LABEL_TEXT_COLOR = "#F8FAFC"
_BADGE_TEXT_COLOR = "#0B1118"
_CROSSHAIR_COLOR = "#64748B"
_CROSSHAIR_LABEL_COLOR = "#334155"
_ZOOM_ICON_SIZE = 18
_ZOOM_ICON_EXTENT = QSize(_ZOOM_ICON_SIZE, _ZOOM_ICON_SIZE)
# The icons are painted rather than shipped as assets so they inherit the
# button's enabled and disabled text colors from the theme.
_ZOOM_ICON_COLOR = "#F1F5F9"
_ZOOM_ICON_DISABLED_COLOR = "#6F7D93"


class CandlestickChartWidget(QWidget):
    """Read-only QPainter surface for a validated chart snapshot."""

    range_requested = Signal(object)
    retry_requested = Signal()
    view_requested = Signal(object)
    latest_requested = Signal()

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
        controls = QHBoxLayout()
        controls.setSpacing(12)
        navigation = QHBoxLayout()
        navigation.setSpacing(8)
        self.zoom_out_button = QPushButton()
        self.zoom_out_button.setObjectName("zoomOutButton")
        self.zoom_out_button.setFixedWidth(44)
        self.zoom_out_button.setAccessibleName("Zoom out chart")
        self.zoom_out_button.setIcon(_magnifier_icon(zoom_in=False))
        self.zoom_out_button.setIconSize(_ZOOM_ICON_EXTENT)
        self.zoom_in_button = QPushButton()
        self.zoom_in_button.setObjectName("zoomInButton")
        self.zoom_in_button.setFixedWidth(44)
        self.zoom_in_button.setAccessibleName("Zoom in chart")
        self.zoom_in_button.setIcon(_magnifier_icon(zoom_in=True))
        self.zoom_in_button.setIconSize(_ZOOM_ICON_EXTENT)
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
        # A chart panned away from the latest candle stops taking live prices,
        # while the Position tab goes on marking on them. The way back is one
        # click rather than a range the reader has to reconstruct.
        self.follow_latest_button = QPushButton("Jump to latest")
        self.follow_latest_button.setObjectName("followLatestButton")
        self.follow_latest_button.setAccessibleName("Jump to latest candle")
        self.follow_latest_button.hide()
        self.previous_range_button.clicked.connect(self._request_previous_range)
        self.next_range_button.clicked.connect(self._request_next_range)
        self.zoom_out_button.clicked.connect(self._request_zoom_out)
        self.zoom_in_button.clicked.connect(self._request_zoom_in)
        self.retry_button.clicked.connect(self.retry_requested)
        self.follow_latest_button.clicked.connect(self.latest_requested)
        self.symbol.currentIndexChanged.connect(self._request_view)
        self.timeframe.currentIndexChanged.connect(self._request_view)
        navigation.addWidget(self.previous_range_button)
        navigation.addWidget(self.next_range_button)
        navigation.addWidget(self.zoom_out_button)
        navigation.addWidget(self.zoom_in_button)
        controls.addLayout(navigation)
        controls.addWidget(self.follow_latest_button)
        controls.addWidget(self.retry_button)
        self._header.addWidget(self.symbol)
        self._header.addSpacing(12)
        self._header.addWidget(self.timeframe)
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

    def set_following_latest(self, following: bool) -> None:
        """Say when the chart has stopped taking the prices as they arrive.

        A frozen chart beside a Position tab that keeps moving reads as a
        fault. It is not one — the reader panned away from the latest candle —
        and this is what says so, and offers the way back.
        """
        self.follow_latest_button.setVisible(not following)

    def show_snapshot(self, snapshot: ChartSnapshot) -> None:
        # A live refresh repaints the same view under the cursor, so the
        # crosshair stays put; only a different view invalidates its position.
        if _view_key(snapshot) != _view_key(self._snapshot):
            self._crosshair_position = None
        self._snapshot = snapshot
        self.setAccessibleName(
            f"Candlestick chart for {snapshot.symbol} {snapshot.timeframe}"
        )
        with QSignalBlocker(self.symbol), QSignalBlocker(self.timeframe):
            self.symbol.setCurrentIndex(self.symbol.findData(snapshot.symbol))
            self.timeframe.setCurrentIndex(self.timeframe.findData(snapshot.timeframe))
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
        painter.setPen(QColor(_AXIS_TEXT_COLOR))
        painter.drawText(surface, _ALIGN_CENTER, message)

    def _draw_chart(
        self, painter: QPainter, surface: QRectF, snapshot: ChartSnapshot
    ) -> None:
        model = build_painter_model(snapshot, surface)
        painter.setPen(QPen(QColor(_GRID_COLOR), 1))
        for line in model.grid_lines:
            painter.drawLine(line)
        painter.setPen(QPen(QColor(_AXIS_TEXT_COLOR), 1))
        for tick in model.price_ticks + model.time_ticks:
            painter.drawLine(tick.line)
            painter.drawText(tick.label_rect, _ALIGN_CENTER, tick.label)
        for candle in model.candles:
            painter.setPen(QPen(candle.color, 1))
            painter.drawLine(candle.wick)
            painter.fillRect(candle.body, candle.color)
        ring_pen = QPen(QColor(CHART_SURFACE_COLOR), MARKER_RING_WIDTH)
        badge_text_pen = QPen(QColor(_BADGE_TEXT_COLOR), 1)
        for marker in model.markers:
            painter.setPen(ring_pen)
            painter.setBrush(marker.color)
            painter.drawPath(_marker_path(marker))
            painter.setPen(badge_text_pen)
            painter.drawText(marker.badge, _ALIGN_CENTER, marker.label)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        current_price = model.current_price
        if current_price is not None:
            painter.setPen(QPen(current_price.color, 1, Qt.PenStyle.DotLine))
            painter.drawLine(current_price.line)
            painter.fillRect(current_price.label_rect, current_price.color)
            painter.setPen(QPen(QColor(_LABEL_TEXT_COLOR), 1))
            painter.drawText(
                current_price.label_rect,
                _ALIGN_CENTER,
                f"{current_price.price_text}\n{current_price.remaining_text}",
            )
        self._draw_rsi_pane(painter, model)
        crosshair = self._crosshair_for(model)
        if crosshair is not None:
            painter.setPen(QPen(QColor(_CROSSHAIR_COLOR), 1, Qt.PenStyle.DashLine))
            painter.drawLine(crosshair.vertical)
            painter.drawLine(crosshair.horizontal)
            self._draw_crosshair_label(painter, crosshair.price_label)
            self._draw_crosshair_label(painter, crosshair.time_label)
        self._draw_legend(painter, model)

    def _draw_rsi_pane(self, painter: QPainter, model: ChartPainterModel) -> None:
        pane = model.rsi_pane
        painter.setPen(QPen(QColor(_GRID_COLOR), 1, Qt.PenStyle.DashLine))
        for guide in pane.guides:
            painter.drawLine(guide.line)
        painter.setPen(QPen(QColor(_AXIS_TEXT_COLOR), 1))
        for guide in pane.guides:
            painter.drawText(guide.label_rect, _ALIGN_CENTER, guide.label)
        painter.setPen(QPen(pane.color, 1))
        for polyline in pane.polylines:
            painter.drawPolyline(QPolygonF(polyline))

    def _draw_legend(self, painter: QPainter, model: ChartPainterModel) -> None:
        readout = self._readout_for(model)
        if readout is None:
            return
        plot = model.plot_surface
        rect = QRectF(plot.left() + 8, plot.top() + 4, max(1.0, plot.width() - 16), 18)
        painter.setPen(QPen(QColor(_LEGEND_TEXT_COLOR), 1))
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
        painter.fillRect(label.label_rect, QColor(_CROSSHAIR_LABEL_COLOR))
        painter.setPen(QPen(QColor(_LABEL_TEXT_COLOR), 1))
        painter.drawText(label.label_rect, _ALIGN_CENTER, label.label)


def _magnifier_icon(*, zoom_in: bool) -> QIcon:
    """A lens with a plus or a minus, in the theme's two button text colors."""
    icon = QIcon()
    icon.addPixmap(
        _magnifier_pixmap(_ZOOM_ICON_COLOR, zoom_in=zoom_in),
        QIcon.Mode.Normal,
    )
    icon.addPixmap(
        _magnifier_pixmap(_ZOOM_ICON_DISABLED_COLOR, zoom_in=zoom_in),
        QIcon.Mode.Disabled,
    )
    return icon


def _magnifier_pixmap(color: str, *, zoom_in: bool) -> QPixmap:
    # Painted at 3x and tagged, so the lens stays smooth on a retina screen.
    ratio = 3
    pixmap = QPixmap(_ZOOM_ICON_SIZE * ratio, _ZOOM_ICON_SIZE * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    lens = QRectF(1.6, 1.6, 11.2, 11.2)
    painter.drawEllipse(lens)
    center = lens.center()
    arm = 2.7
    painter.drawLine(
        QPointF(center.x() - arm, center.y()),
        QPointF(center.x() + arm, center.y()),
    )
    if zoom_in:
        painter.drawLine(
            QPointF(center.x(), center.y() - arm),
            QPointF(center.x(), center.y() + arm),
        )
    # The handle leaves the lens along the diagonal through its center.
    painter.drawLine(QPointF(11.7, 11.7), QPointF(15.8, 15.8))
    painter.end()
    return pixmap


def _marker_path(marker: MarkerGlyph) -> QPainterPath:
    """The badge body and its tip as one outline, so the ring traces both."""
    path = QPainterPath()
    path.addRoundedRect(marker.badge, MARKER_BADGE_RADIUS, MARKER_BADGE_RADIUS)
    tip = QPainterPath()
    tip.addPolygon(QPolygonF(marker.pointer))
    tip.closeSubpath()
    return path.united(tip)


def _view_key(snapshot: ChartSnapshot | None) -> tuple[str, str, ChartRange] | None:
    """What the cursor is pointing at: same key means the same axes."""
    if snapshot is None:
        return None
    return (snapshot.symbol, snapshot.timeframe, snapshot.chart_range)


def _legend_text(readout: CandleReadout) -> str:
    candle = readout.candle
    rsi = "—" if readout.rsi is None else f"{readout.rsi:,.2f}"
    # ATR is a distance in price, so it reads in the same units as OHLC.
    atr = "—" if readout.atr is None else f"{readout.atr:,.2f}"
    return (
        f"{in_display_timezone(candle.open_time):%Y-%m-%d %H:%M}  "
        f"O {candle.open:,.2f}  H {candle.high:,.2f}  "
        f"L {candle.low:,.2f}  C {candle.close:,.2f}  "
        f"RSI({readout.rsi_period}) {rsi}  "
        f"ATR({readout.atr_period}) {atr}"
    )
