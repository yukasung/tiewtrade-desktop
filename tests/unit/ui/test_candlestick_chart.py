from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QWheelEvent
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

import tiewtrade.ui.chart_painter_model as chart_module
from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.application.chart_indicators import RsiSeries
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.trade_history import FillSide, TradeFill
from tiewtrade.ui.candlestick_chart import CandlestickChartWidget


def test_painter_model_is_pure_and_widget_imports_its_public_api() -> None:
    model_source = Path("src/tiewtrade/ui/chart_painter_model.py").read_text()
    widget_source = Path("src/tiewtrade/ui/candlestick_chart.py").read_text()

    assert "QWidget" not in model_source
    assert "QPainter" not in model_source
    assert "from tiewtrade.ui.chart_painter_model import" in widget_source


def test_chart_renders_durable_buy_and_sell_markers_without_action_buttons(
    qtbot: QtBot,
) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.show_snapshot(ready_snapshot())
    chart.show()

    assert chart.accessibleName() == "Candlestick chart for BTCUSDT 5m"
    assert chart._facts.text() == "BTCUSDT · 5m · Paper"
    assert chart.marker_labels == ("B", "S")
    assert chart.findChildren(QPushButton, "manualOrderButton") == []
    assert not chart.grab().isNull()


def test_chart_header_emits_selected_read_only_view(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    selected: list[ChartViewSelection] = []
    chart.view_requested.connect(selected.append)

    chart.symbol.setCurrentIndex(chart.symbol.findData("BNBUSDT"))
    chart.timeframe.setCurrentIndex(chart.timeframe.findData("1m"))

    assert selected[-1] == ChartViewSelection(symbol="BNBUSDT", timeframe="1m")


def test_zoom_buttons_request_a_narrower_and_wider_range(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    snapshot = zoomable_snapshot()
    chart.show_snapshot(snapshot)
    requested: list[ChartRange] = []
    chart.range_requested.connect(requested.append)

    chart.zoom_in_button.click()
    chart.zoom_out_button.click()

    assert len(requested) == 2
    zoomed_in, zoomed_out = requested
    assert zoomed_in.end == snapshot.chart_range.end
    assert zoomed_out.end == snapshot.chart_range.end
    assert zoomed_in.end - zoomed_in.start < (
        snapshot.chart_range.end - snapshot.chart_range.start
    )
    assert zoomed_out.end - zoomed_out.start > (
        snapshot.chart_range.end - snapshot.chart_range.start
    )


def test_mouse_wheel_zooms_the_chart_range(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    snapshot = zoomable_snapshot()
    chart.show_snapshot(snapshot)
    requested: list[ChartRange] = []
    chart.range_requested.connect(requested.append)

    _send_wheel(chart, delta=120)
    _send_wheel(chart, delta=-120)

    assert len(requested) == 2
    assert requested[0].end - requested[0].start < (
        snapshot.chart_range.end - snapshot.chart_range.start
    )
    assert requested[1].end - requested[1].start > (
        snapshot.chart_range.end - snapshot.chart_range.start
    )


def test_zoom_is_inert_without_a_readable_snapshot(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    requested: list[ChartRange] = []
    chart.range_requested.connect(requested.append)

    _send_wheel(chart, delta=120)
    chart.zoom_in_button.click()

    assert requested == []


def test_chart_selector_header_leaves_readable_space_between_controls(
    qtbot: QtBot,
) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(1200, 500)
    chart.show_snapshot(ready_snapshot())
    chart.show()

    assert chart.timeframe.geometry().left() - chart.symbol.geometry().right() - 1 == 12
    assert chart._facts.geometry().left() - chart.timeframe.geometry().right() - 1 == 16


def test_painter_model_contains_only_chart_presentation_facts() -> None:
    snapshot = ready_snapshot(
        candles=(
            candle(0, open_price="100", close_price="103"),
            candle(5, open_price="103", close_price="101"),
            candle(10, open_price="101", close_price="104"),
        )
    )

    model = chart_module.build_painter_model(snapshot, QRectF(0, 0, 300, 200))

    assert [field.name for field in fields(chart_module.CandleGlyph)] == [
        "open_time",
        "x",
        "wick",
        "body",
        "color",
    ]
    assert [field.name for field in fields(chart_module.ChartPainterModel)] == [
        "plot_surface",
        "price_low",
        "price_high",
        "visual_range",
        "grid_lines",
        "price_ticks",
        "time_ticks",
        "candles",
        "markers",
        "current_price",
        "rsi_pane",
    ]
    assert min(min(glyph.wick.y1(), glyph.wick.y2()) for glyph in model.candles) > (
        model.plot_surface.top()
    )
    assert max(max(glyph.wick.y1(), glyph.wick.y2()) for glyph in model.candles) < (
        model.plot_surface.bottom()
    )
    buy, sell = model.markers
    assert buy.label == "B"
    assert sell.label == "S"
    assert all(glyph.body.width() > 0 for glyph in model.candles)


def test_fill_badges_never_cover_candle_bodies_or_wicks() -> None:
    """A fill's price sits inside its candle, so the badge must not use it."""
    model = chart_module.build_painter_model(ready_snapshot(), QRectF(0, 0, 600, 400))

    assert model.markers
    for marker in model.markers:
        for glyph in model.candles:
            assert not marker.badge.intersects(glyph.body), (
                f"{marker.label} badge covers a candle body"
            )
            wick = QRectF(
                glyph.wick.x1() - 0.5,
                min(glyph.wick.y1(), glyph.wick.y2()),
                1.0,
                abs(glyph.wick.y2() - glyph.wick.y1()),
            )
            assert not marker.badge.intersects(wick), (
                f"{marker.label} badge covers a candle wick"
            )


def test_badges_on_neighbouring_fills_do_not_overlap_each_other() -> None:
    fills = tuple(
        fill(
            f"f{minute}",
            FillSide.BUY if minute % 10 == 0 else FillSide.SELL,
            datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        )
        for minute in (0, 5, 10, 15)
    )

    model = chart_module.build_painter_model(
        ready_snapshot(fills=fills), QRectF(0, 0, 600, 400)
    )

    badges = [marker.badge for marker in model.markers]
    assert len(badges) == 4
    for index, badge in enumerate(badges):
        for other in badges[index + 1 :]:
            assert not badge.intersects(other), f"badges overlap: {badge} {other}"


def test_painter_model_reserves_axis_space_and_exposes_ticks() -> None:
    model = chart_module.build_painter_model(ready_snapshot(), QRectF(0, 0, 600, 300))

    assert model.plot_surface.right() < 600
    assert model.plot_surface.bottom() < 300
    assert len(model.price_ticks) == 5
    assert len(model.time_ticks) > 6
    assert all("UTC" not in tick.label for tick in model.time_ticks)
    assert all(len(tick.label) == 5 for tick in model.time_ticks)
    assert all(
        left.label_rect.right() <= right.label_rect.left()
        for left, right in zip(model.time_ticks, model.time_ticks[1:], strict=False)
    )


def test_mouse_over_plot_shows_crosshair_price_and_time(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(640, 360)
    chart.show_snapshot(ready_snapshot())
    chart.show()

    cast(Any, qtbot).mouseMove(chart, QPoint(280, 180))

    assert chart.crosshair is not None
    assert chart.crosshair.price_label.label
    assert chart.crosshair.time_label.label.endswith("UTC")


def test_crosshair_hides_outside_plot_area(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(640, 360)
    chart.show_snapshot(ready_snapshot())
    chart.show()

    cast(Any, qtbot).mouseMove(chart, QPoint(300, 180))
    assert chart.crosshair is not None
    cast(Any, qtbot).mouseMove(chart, QPoint(chart.width() - 4, 20))

    assert chart.crosshair is None


def test_candles_and_markers_remain_inside_plot_not_axis_space() -> None:
    model = chart_module.build_painter_model(ready_snapshot(), QRectF(0, 0, 600, 300))

    assert all(
        model.plot_surface.contains(glyph.body.center()) for glyph in model.candles
    )
    assert all(
        model.plot_surface.contains(marker.badge.center()) for marker in model.markers
    )


def test_chart_geometry_pads_extrema_and_keeps_end_candle_inside_plot() -> None:
    model = chart_module.build_painter_model(
        ready_snapshot(candles=(candle(0), candle(15))), QRectF(0, 0, 600, 300)
    )

    assert (
        min(min(glyph.wick.y1(), glyph.wick.y2()) for glyph in model.candles)
        > model.plot_surface.top()
    )
    assert (
        max(max(glyph.wick.y1(), glyph.wick.y2()) for glyph in model.candles)
        < model.plot_surface.bottom()
    )
    assert all(model.plot_surface.contains(glyph.body) for glyph in model.candles)
    assert all(
        model.plot_surface.left() <= glyph.wick.x1() <= model.plot_surface.right()
        for glyph in model.candles
    )


def test_snapshot_change_clears_crosshair(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(640, 360)
    chart.show_snapshot(ready_snapshot())
    chart.show()
    cast(Any, qtbot).mouseMove(chart, QPoint(280, 180))
    assert chart.crosshair is not None

    chart.show_snapshot(ready_snapshot())

    assert chart.crosshair is None


def test_painter_draws_forming_candle_in_normal_bullish_style() -> None:
    model = chart_module.build_painter_model(
        ready_snapshot(
            candles=(candle(15, close_price="101"),),
            in_progress_candle=candle(
                20,
                close_price="103",
                high_price="120",
            ),
        ),
        QRectF(0, 0, 600, 300),
    )
    assert len(model.candles) == 2
    assert model.candles[-1].color == QColor("#0ECB81")
    assert model.candles[-1].body.center().x() < 600
    assert (
        min(model.candles[-1].wick.y1(), model.candles[-1].wick.y2())
        > model.plot_surface.top()
    )


def test_forming_candle_exposes_colored_current_price_label() -> None:
    model = chart_module.build_painter_model(
        ready_snapshot(in_progress_candle=candle(20, close_price="103")),
        QRectF(0, 0, 600, 300),
    )

    assert model.current_price is not None
    assert model.current_price.price_text == "103.00"
    assert model.current_price.remaining_text == "05:00"
    assert model.current_price.color == QColor("#0ECB81")
    assert model.plot_surface.contains(model.current_price.line.p1())
    assert model.plot_surface.contains(model.current_price.line.p2())
    assert model.current_price.label_rect.left() >= model.plot_surface.right()


def test_marker_labels_stay_visible_at_price_extrema_at_minimum_height(
    qtbot: QtBot,
) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(640, chart.minimumHeight())
    snapshot = ready_snapshot(
        fills=(
            fill(
                "buy-high",
                FillSide.BUY,
                datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                price="110",
            ),
            fill(
                "sell-low",
                FillSide.SELL,
                datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
                price="90",
            ),
        )
    )
    surface = QRectF(12, 54, chart.width() - 24, chart.height() - 66)

    model = chart_module.build_painter_model(snapshot, surface)

    visible_bounds = QRectF(chart.rect())
    assert [marker.label for marker in model.markers] == ["B", "S"]
    for marker in model.markers:
        assert visible_bounds.contains(marker.badge), (
            f"{marker.label} badge is outside the chart: {marker.badge}"
        )


def test_previous_and_next_range_controls_are_keyboard_accessible(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(1200, 500)
    chart.show()
    chart.show_snapshot(ready_snapshot())
    requests: list[ChartRange] = []
    chart.range_requested.connect(requests.append)

    chart.next_range_button.setFocus()
    cast(Any, qtbot).keyClick(chart.next_range_button, Qt.Key.Key_Space)
    chart.previous_range_button.setFocus()
    cast(Any, qtbot).keyClick(chart.previous_range_button, Qt.Key.Key_Space)

    assert chart.previous_range_button.text() == "←"
    assert chart.next_range_button.text() == "→"
    assert chart.previous_range_button.width() == 44
    assert chart.next_range_button.width() == 44
    assert chart.previous_range_button.accessibleName() == "Previous chart range"
    assert chart.next_range_button.accessibleName() == "Next chart range"
    assert (
        chart.previous_range_button.geometry().left()
        - chart._range.geometry().right()
        - 1
        == 12
    )
    assert (
        chart.next_range_button.geometry().left()
        - chart.previous_range_button.geometry().right()
        - 1
        == 8
    )
    surface = chart._chart_surface()
    assert surface.top() >= chart.previous_range_button.geometry().bottom() + 24
    assert surface.top() >= chart.next_range_button.geometry().bottom() + 24
    assert requests == [
        ChartRange(
            datetime(2025, 12, 31, 23, 40, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        ),
    ]


def test_next_range_is_disabled_at_latest_completed_boundary(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    snapshot = ready_snapshot()
    chart.show_snapshot(snapshot)
    requests: list[ChartRange] = []
    chart.range_requested.connect(requests.append)

    assert snapshot.chart_range.end == snapshot.observed_at_utc
    assert not chart.next_range_button.isEnabled()
    chart.next_range_button.click()
    assert requests == []


def test_next_range_is_limited_to_latest_completed_boundary(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    session = configured_spot_session()
    selected_range = ChartRange(
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
    )
    chart.show_snapshot(
        ChartSnapshot(
            session=session,
            chart_range=selected_range,
            observed_at_utc=datetime(2026, 1, 1, 0, 23, tzinfo=UTC),
            candles=(),
            fills=(),
            state=ChartReadState.EMPTY,
        )
    )
    requests: list[ChartRange] = []
    chart.range_requested.connect(requests.append)

    chart.next_range_button.click()

    assert requests == [
        ChartRange(
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
        )
    ]


def test_unavailable_chart_exposes_chart_specific_retry(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    snapshot = ready_snapshot()
    chart.show_snapshot(
        ChartSnapshot(
            _session_facts=(snapshot.session_id, snapshot.symbol, snapshot.timeframe),
            chart_range=snapshot.chart_range,
            observed_at_utc=snapshot.observed_at_utc,
            candles=(),
            fills=(),
            state=ChartReadState.UNAVAILABLE,
            message="Chart is unavailable",
        )
    )
    retries: list[bool] = []
    chart.retry_requested.connect(lambda: retries.append(True))

    assert chart.retry_button.isVisibleTo(chart)
    assert chart.retry_button.accessibleName() == "Retry chart"
    chart.retry_button.click()

    assert retries == [True]


def zoomable_snapshot() -> ChartSnapshot:
    """A window wide enough that both zoom directions stay within limits."""
    end = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    chart_range = ChartRange(end - timedelta(minutes=5) * 120, end)
    return ChartSnapshot(
        session=configured_spot_session(),
        chart_range=chart_range,
        observed_at_utc=end,
        candles=(),
        fills=(),
        state=ChartReadState.EMPTY,
    )


def _send_wheel(chart: CandlestickChartWidget, *, delta: int) -> None:
    event = QWheelEvent(
        QPointF(chart.rect().center()),
        QPointF(chart.mapToGlobal(chart.rect().center())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    chart.wheelEvent(event)


def test_rsi_pane_sits_below_the_price_pane_with_reference_levels() -> None:
    snapshot = ready_snapshot(rsi=rsi_at((0, "45.5"), (5, "52.25"), (10, "61")))

    model = chart_module.build_painter_model(snapshot, QRectF(0, 0, 600, 400))

    pane = model.rsi_pane
    assert pane.surface.top() > model.plot_surface.bottom()
    assert [guide.label for guide in pane.guides] == ["30", "50", "70"]
    assert [point.y() for point in pane.polylines[0]] == sorted(
        (point.y() for point in pane.polylines[0]), reverse=True
    )
    assert all(
        pane.surface.top() <= point.y() <= pane.surface.bottom()
        for polyline in pane.polylines
        for point in polyline
    )


def test_rsi_line_breaks_rather_than_bridging_the_warm_up_gap() -> None:
    # Only the last two candles have a value, so no line may reach back to
    # the first one as though it had been measured.
    snapshot = ready_snapshot(rsi=rsi_at((5, "52.25"), (10, "61")))

    model = chart_module.build_painter_model(snapshot, QRectF(0, 0, 600, 400))

    assert len(model.rsi_pane.polylines) == 1
    assert len(model.rsi_pane.polylines[0]) == 2


def test_legend_reads_the_latest_candle_until_the_mouse_picks_one(
    qtbot: QtBot,
) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(600, 400)
    chart.show_snapshot(
        ready_snapshot(rsi=rsi_at((0, "45.5"), (5, "52.25"), (10, "61")))
    )
    chart.show()

    latest = chart.readout
    assert latest is not None
    assert latest.candle.open_time == datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    assert latest.rsi == Decimal("61")

    model = chart_module.build_painter_model(ready_snapshot(), chart._chart_surface())
    first = model.candles[0].body.center()
    cast(Any, qtbot).mouseMove(
        chart,
        QPoint(int(first.x()), int(model.plot_surface.center().y())),
    )

    hovered = chart.readout
    assert hovered is not None
    assert hovered.candle.open_time == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert hovered.rsi == Decimal("45.5")


def test_legend_marks_a_candle_still_inside_the_rsi_warm_up(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    chart.resize(600, 400)
    chart.show_snapshot(ready_snapshot())
    chart.show()

    readout = chart.readout

    assert readout is not None
    assert readout.rsi is None
    assert not chart.grab().isNull()


def ready_snapshot(
    *,
    candles: tuple[Candle, ...] | None = None,
    fills: tuple[TradeFill, ...] | None = None,
    in_progress_candle: Candle | None = None,
    rsi: RsiSeries | None = None,
) -> ChartSnapshot:
    session = configured_spot_session()
    chart_range = ChartRange(
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
    )
    buy = fill("buy-1", FillSide.BUY, datetime(2026, 1, 1, 0, 5, tzinfo=UTC))
    sell = fill("sell-1", FillSide.SELL, datetime(2026, 1, 1, 0, 10, tzinfo=UTC))
    return ChartSnapshot(
        session=session,
        chart_range=chart_range,
        observed_at_utc=chart_range.end,
        candles=candles or (candle(0), candle(5), candle(10)),
        fills=fills or (buy, sell),
        state=ChartReadState.READY,
        in_progress_candle=in_progress_candle,
        rsi=rsi or RsiSeries(),
    )


def rsi_at(*minute_values: tuple[int, str]) -> RsiSeries:
    """A series holding exactly these values, as if the rest were warm-up."""
    return RsiSeries(
        values={
            datetime(2026, 1, 1, 0, minute, tzinfo=UTC): Decimal(value)
            for minute, value in minute_values
        }
    )


def candle(
    minute: int,
    *,
    open_price: str = "100",
    close_price: str = "103",
    high_price: str | None = None,
    low_price: str = "99",
) -> Candle:
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=open_value,
        high=Decimal(high_price)
        if high_price is not None
        else max(open_value, close_value) + Decimal("3"),
        low=Decimal(low_price),
        close=close_value,
    )


def fill(
    fill_id: str,
    side: FillSide,
    filled_at_utc: datetime,
    *,
    price: str = "102",
) -> TradeFill:
    from tests.support.trade_history_records import trade_fill

    return trade_fill(
        fill_id=fill_id,
        side=side,
        session_id=configured_spot_session().config.session_id,
        filled_at_utc=filled_at_utc,
        price=Decimal(price),
        quantity=Decimal("2"),
        notional=Decimal(price) * Decimal("2"),
    )
