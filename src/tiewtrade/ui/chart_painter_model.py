from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import QLineF, QPointF, QRectF
from PySide6.QtGui import QColor

from tiewtrade.application.chart_data import (
    ChartCandle,
    ChartRange,
    ChartSnapshot,
)
from tiewtrade.trading.trade_history import FillSide
from tiewtrade.ui.display_time import in_display_timezone

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
_MARKER_BADGE_EXTENT = (_MARKER_BADGE_SIZE, _MARKER_BADGE_SIZE)
# The badge carries a tip on the side facing its candle, so it reads as a
# pin aimed at one candle rather than a chip floating near several.
_MARKER_TIP_HEIGHT = 5.0
_MARKER_TIP_WIDTH = 9.0
# The tip starts inside the badge so the two merge into one outline.
_MARKER_TIP_OVERLAP = 2.0
# Clear air between the tip and the candles, so the pin never reads as ink
# attached to a candle.
_MARKER_TIP_CLEARANCE = 9.0
_MARKER_ANCHOR_GAP = _MARKER_TIP_HEIGHT + _MARKER_TIP_CLEARANCE
_MARKER_STACK_GAP = 3.0
_MARKER_STACK_LIMIT = 6
_MARKER_HEADROOM = _MARKER_ANCHOR_GAP + _MARKER_BADGE_SIZE + 2.0
_BASE_PRICE_PADDING = Decimal("0.05")
_MAXIMUM_PRICE_PADDING = Decimal("0.2")
MARKER_BADGE_RADIUS = 5.0
# Badges carry a ring in the surface color so they stay legible where they
# cross a candle or another badge, instead of a border adding data-weight ink.
MARKER_RING_WIDTH = 2.0
CHART_SURFACE_COLOR = "#10151C"


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
    badge: QRectF
    # The tip that aims the badge at its candle, as a triangle whose base sits
    # inside the badge body so the two paint as one shape. `badge` stays the
    # body alone: it is what the label centers in and what badges stack against.
    pointer: tuple[QPointF, ...]
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
    # Read beside the RSI and never drawn: ATR is a distance in price, so it
    # has no pane of its own to sit in.
    atr: Decimal | None
    atr_period: int


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
    visual_range = snapshot.visible_range
    prices = [
        price for candle in display_candles for price in (candle.high, candle.low)
    ] + [marker.price for marker in snapshot.markers]
    price_surface, rsi_surface = _panes(surface)
    low, high = _padded_price_range(
        min(prices),
        max(prices),
        # Reserve room outside the extreme candles for their badges, so the
        # annotation layer never has to sit on the price ink.
        headroom=_MARKER_HEADROOM if snapshot.markers else 0.0,
        pane_height=price_surface.height(),
    )
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
    markers = _marker_glyphs(snapshot, candles, low, high, price_surface)
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
        markers=markers,
        current_price=current_price,
        rsi_pane=_rsi_pane_glyph(rsi_values, candles, rsi_surface, surface),
    )


def _marker_glyphs(
    snapshot: ChartSnapshot,
    candles: Sequence[CandleGlyph],
    low: Decimal,
    high: Decimal,
    price_surface: QRectF,
) -> tuple[MarkerGlyph, ...]:
    """Place fill badges off their own candle, clear of one another.

    A fill happens at a price inside its candle's range, so a badge drawn at
    that price always lands on the candle. Anchoring to the candle's outer
    edge instead — low for a buy, high for a sell — keeps the body and wick
    readable, and the badge's tip names the candle it belongs to.

    Only the named candle is kept clear: a badge is wider than a candle, so
    demanding room from every neighbour it spans would fling it far from the
    fill it marks. It may sit over the candles beside it.
    """
    glyph_by_open_time = {glyph.open_time: glyph for glyph in candles}
    half = _MARKER_BADGE_SIZE / 2
    placed: list[QRectF] = []
    glyphs: list[MarkerGlyph] = []
    for marker in sorted(snapshot.markers, key=lambda item: item.filled_at_utc):
        is_buy = marker.side is FillSide.BUY
        candle = glyph_by_open_time.get(marker.candle_open_time)
        if candle is None:
            # The candle scrolled out of view; the fill price is all that is left.
            fill_y = _price_y(marker.price, low, high, price_surface)
            below_y = above_y = fill_y
            unclamped_x = _marker_x(
                marker.filled_at_utc, snapshot.visible_range, price_surface
            )
        else:
            below_y = max(candle.wick.y1(), candle.wick.y2())
            above_y = min(candle.wick.y1(), candle.wick.y2())
            # Over the candle it marks, not the instant it was stamped with.
            unclamped_x = candle.x
        x = _clamp_x(unclamped_x, price_surface, half)
        badge, below = _free_badge(
            x,
            below_y=below_y,
            above_y=above_y,
            prefer_below=is_buy,
            placed=placed,
            pane=price_surface,
        )
        placed.append(badge)
        glyphs.append(
            MarkerGlyph(
                badge=badge,
                pointer=_badge_pointer(badge, below=below),
                label=marker_label(marker.side),
                color=QColor("#0ECB81" if is_buy else "#F6465D"),
            )
        )
    return tuple(glyphs)


def _badge_pointer(badge: QRectF, *, below: bool) -> tuple[QPointF, ...]:
    """The badge's tip, aimed at the candle it names."""
    center_x = badge.center().x()
    edge = badge.top() if below else badge.bottom()
    outward = -1.0 if below else 1.0
    base_y = edge - outward * _MARKER_TIP_OVERLAP
    return (
        QPointF(center_x - _MARKER_TIP_WIDTH / 2, base_y),
        QPointF(center_x + _MARKER_TIP_WIDTH / 2, base_y),
        QPointF(center_x, edge + outward * _MARKER_TIP_HEIGHT),
    )


def _free_badge(
    x: float,
    *,
    below_y: float,
    above_y: float,
    prefer_below: bool,
    placed: Sequence[QRectF],
    pane: QRectF,
) -> tuple[QRectF, bool]:
    """Find a spot for the badge that is off the candle and off other badges.

    Buys hang under the candle's low and sells ride over its high. When that
    side has no room left in the pane, the badge flips to the other side of
    the same candle rather than being clamped back onto the price ink.
    """
    for below in (prefer_below, not prefer_below):
        badge = _stacked_badge(x, below_y if below else above_y, below, placed)
        if pane.top() <= badge.top() and badge.bottom() <= pane.bottom():
            return badge, below
    badge = _stacked_badge(
        x,
        below_y if prefer_below else above_y,
        prefer_below,
        placed,
    )
    return (
        QRectF(
            badge.left(),
            min(max(badge.top(), pane.top()), pane.bottom() - _MARKER_BADGE_SIZE),
            *_MARKER_BADGE_EXTENT,
        ),
        prefer_below,
    )


def _stacked_badge(
    x: float,
    anchor_y: float,
    below: bool,
    placed: Sequence[QRectF],
) -> QRectF:
    """Step further from the candle until the badge clears the earlier ones."""
    outward = 1.0 if below else -1.0
    step = _MARKER_BADGE_SIZE + _MARKER_STACK_GAP
    top = (
        anchor_y + _MARKER_ANCHOR_GAP
        if below
        else anchor_y - _MARKER_ANCHOR_GAP - _MARKER_BADGE_SIZE
    )
    badge = QRectF(x - _MARKER_BADGE_SIZE / 2, top, *_MARKER_BADGE_EXTENT)
    for _ in range(_MARKER_STACK_LIMIT):
        if not any(badge.intersects(other) for other in placed):
            break
        badge = badge.translated(0.0, outward * step)
    return badge


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
    return CandleReadout(
        candle=candle,
        rsi=snapshot.rsi_for(candle.open_time),
        rsi_period=snapshot.rsi.period,
        atr=snapshot.atr_for(candle.open_time),
        atr_period=snapshot.atr.period,
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


def _padded_price_range(
    low: Decimal,
    high: Decimal,
    *,
    headroom: float = 0.0,
    pane_height: float = 1.0,
) -> tuple[Decimal, Decimal]:
    """Pad the price range, widening it to keep `headroom` pixels at each end.

    Padding is a fraction of the *padded* span, so the fraction that buys
    `headroom` pixels is solved for rather than applied to the raw span.
    """
    if low == high:
        low -= Decimal("1")
        high += Decimal("1")
    fraction = max(
        _BASE_PRICE_PADDING,
        min(_MAXIMUM_PRICE_PADDING, Decimal(str(headroom / max(pane_height, 1.0)))),
    )
    padding = (high - low) * fraction / (Decimal("1") - fraction * Decimal("2"))
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
            label=f"{in_display_timezone(timestamp):%Y-%m-%d %H:%M}",
            value=timestamp.isoformat(),
        ),
    )


def _price_text(value: Decimal) -> str:
    return f"{value:,.2f}"


def _time_text(value: datetime) -> str:
    return f"{in_display_timezone(value):%H:%M}"


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
