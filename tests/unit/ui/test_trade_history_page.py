from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QWidget
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click, qdate, table_item
from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.ui.trade_history_page import TradeHistoryPage
from tiewtrade.ui.trade_history_presenter import TradeHistoryFilterValues


def history_result(
    *items: object,
    net_realized_pnl: Decimal = Decimal("19.58"),
) -> BasketHistoryResult:
    return BasketHistoryResult(
        items=items,  # type: ignore[arg-type]
        net_realized_pnl=net_realized_pnl,
    )


def widget_rect_in_parent(widget: QWidget, parent: QWidget) -> QRect:
    return QRect(widget.mapTo(parent, QPoint(0, 0)), widget.size())


def test_page_exposes_filters_and_exact_table_columns(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    assert page.basket_headers == (
        "Opened At",
        "Mode",
        "Market",
        "Symbol",
        "Timeframe",
        "Entries",
        "Notional",
        "Gross PnL",
        "Fees",
        "Funding Fee",
        "Net PnL",
        "Status",
    )
    assert page.fill_headers == (
        "Filled At",
        "Symbol",
        "Side",
        "Price",
        "Quantity",
        "Fee",
        "Total",
        "Realized PnL",
        "Source",
    )
    assert page.basket_table.columnCount() == len(page.basket_headers)
    basket_headers: list[str] = []
    for index in range(page.basket_table.columnCount()):
        item = page.basket_table.horizontalHeaderItem(index)
        assert item is not None
        basket_headers.append(item.text())
    assert tuple(basket_headers) == page.basket_headers
    assert page.fill_table.columnCount() == len(page.fill_headers)
    fill_headers: list[str] = []
    for index in range(page.fill_table.columnCount()):
        item = page.fill_table.horizontalHeaderItem(index)
        assert item is not None
        fill_headers.append(item.text())
    assert tuple(fill_headers) == page.fill_headers
    assert [page.symbol.itemText(index) for index in range(page.symbol.count())] == [
        "All",
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
    ]
    assert [
        page.timeframe.itemText(index) for index in range(page.timeframe.count())
    ] == ["All", "1m", "3m", "5m", "15m", "30m", "1h", "4h"]
    assert [page.market.itemText(index) for index in range(page.market.count())] == [
        "All",
        "Spot",
        "Futures",
    ]
    assert [page.mode.itemText(index) for index in range(page.mode.count())] == [
        "All",
        "Paper",
        "Live",
    ]
    assert [page.status.itemText(index) for index in range(page.status.count())] == [
        "All",
        "Open",
        "Closed",
    ]
    assert page.from_date_enabled.text() == "From Date"
    assert page.to_date_enabled.text() == "To Date"
    assert page.from_date.accessibleName() == "From Date"
    assert page.to_date.accessibleName() == "To Date"


def test_page_uses_two_tabs_hides_trade_only_status_and_has_no_pagination(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()

    assert page.history_tabs.tabText(0) == "Basket History"
    assert page.history_tabs.tabText(1) == "Trade History"

    page.history_tabs.setCurrentIndex(1)

    assert page.status.isHidden()
    assert page.status_label.isHidden()
    assert page.fill_table.isVisible()
    assert not page.basket_table.isVisible()


def test_basket_and_trade_results_render_independently(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="ETHUSDT",
        timeframe="15m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    page.show_baskets(BasketHistoryResult((basket,), Decimal("19.58")))
    page.history_tabs.setCurrentIndex(1)
    page.show_trades((item,))

    assert page.total_items.text() == "1 total Baskets"
    assert page.fill_table.rowCount() == 1
    assert table_item(page.fill_table, 0, 1).text() == "ETHUSDT"
    assert table_item(page.fill_table, 0, 6).text().endswith(" USDT")
    assert table_item(page.fill_table, 0, 2).foreground().color().name() == "#0ecb81"

    page.history_tabs.setCurrentIndex(0)
    page.show_baskets(BasketHistoryResult((basket,), Decimal("19.58")))
    page.history_tabs.setCurrentIndex(1)

    assert page.fill_table.rowCount() == 1
    assert table_item(page.fill_table, 0, 1).text() == "ETHUSDT"

    page.show_trades_empty()

    assert page.fill_empty_state.text() == "No trade history"


def test_initial_basket_failure_preserves_trade_tab_result(qtbot: QtBot) -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show_trades((item,))

    page.show_baskets_unavailable("Basket History unavailable")

    assert page.fill_table.rowCount() == 1


def test_inactive_basket_loading_does_not_lock_trade_filters(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.set_baskets_loading(True)
    assert not page.apply_button.isEnabled()

    page.history_tabs.setCurrentIndex(1)

    assert page.apply_button.isEnabled()
    page.set_trades_loading(True)
    assert not page.apply_button.isEnabled()


def test_page_scrolls_vertically_without_squashing_filter_controls(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1200, 240)
    page.show()

    vertical = page.scroll_area.verticalScrollBar()
    horizontal = page.scroll_area.horizontalScrollBar()
    qtbot.waitUntil(lambda: vertical.maximum() > 0)

    filter_card = page.from_date.parentWidget()
    assert filter_card is not None
    controls = (
        page.symbol,
        page.timeframe,
        page.market,
        page.mode,
        page.status,
        page.from_date_enabled,
        page.from_date,
        page.to_date_enabled,
        page.to_date,
        page.reset_button,
        page.apply_button,
    )
    control_rects = [
        widget_rect_in_parent(control, filter_card) for control in controls
    ]

    assert all(rect.width() > 0 and rect.height() > 0 for rect in control_rects)
    assert all(
        not left.intersects(right)
        for index, left in enumerate(control_rects)
        for right in control_rects[index + 1 :]
    )
    assert vertical.maximum() > 0
    assert horizontal.maximum() == 0

    page.scroll_area.ensureWidgetVisible(page.fill_table)
    qtbot.waitUntil(lambda: vertical.value() > 0)


def test_page_avoids_unnecessary_scrollbars_when_content_fits(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1600, 1000)
    page.show()

    qtbot.waitUntil(lambda: page.scroll_area.viewport().height() > 0)

    assert (
        page.scroll_area.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert (
        page.scroll_area.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert page.scroll_area.verticalScrollBar().maximum() == 0
    assert page.scroll_area.horizontalScrollBar().maximum() == 0


def test_keyboard_focus_skips_scroll_area_and_scrolls_to_lower_table(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1200, 240)
    page.show()

    scroll_bar = page.scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0)
    page.apply_button.setFocus()
    qtbot.waitUntil(page.apply_button.hasFocus)

    QTest.keyClick(page.apply_button, Qt.Key.Key_Tab)

    assert page.history_tabs.hasFocus()

    QTest.keyClick(page.history_tabs, Qt.Key.Key_Tab)

    assert page.basket_table.hasFocus()
    qtbot.waitUntil(lambda: scroll_bar.value() > 0)

    page.fill_table.setFocus()
    QTest.keyClick(page.fill_table, Qt.Key.Key_Tab)

    assert page.symbol.hasFocus()
    assert not page.scroll_area.hasFocus()


def test_apply_and_reset_emit_immutable_filter_values(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    emitted: list[TradeHistoryFilterValues] = []
    resets: list[None] = []
    page.apply_filters_requested.connect(emitted.append)
    page.reset_requested.connect(lambda: resets.append(None))
    page.symbol.setCurrentIndex(page.symbol.findData("BTCUSDT"))

    click(page.apply_button)
    assert emitted == [TradeHistoryFilterValues(symbol="BTCUSDT")]

    click(page.reset_button)
    assert resets == [None]
    assert emitted == [TradeHistoryFilterValues(symbol="BTCUSDT")]
    assert page.filter_values() == TradeHistoryFilterValues()


def test_optional_dates_are_disabled_by_default_and_emitted_when_enabled(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    assert not page.from_date.isEnabled()
    assert not page.to_date.isEnabled()

    page.from_date_enabled.setChecked(True)
    page.to_date_enabled.setChecked(True)
    page.from_date.setDate(qdate(date(2026, 1, 2)))
    page.to_date.setDate(qdate(date(2026, 1, 3)))

    assert page.filter_values() == TradeHistoryFilterValues(
        from_date=date(2026, 1, 2),
        to_date=date(2026, 1, 3),
    )


def test_tables_are_read_only_row_selection_surfaces(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    for table in (page.basket_table, page.fill_table):
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert table.accessibleName()
    # Nothing is read from a selected row, so neither table offers one.
    assert (
        page.basket_table.selectionMode() == QAbstractItemView.SelectionMode.NoSelection
    )


@pytest.mark.parametrize(
    ("value", "semantic"),
    [
        (Decimal("1"), "Profit"),
        (Decimal("-1"), "Loss"),
        (Decimal("0"), "Break-even"),
    ],
)
def test_summary_distinguishes_profit_loss_and_break_even_without_color(
    qtbot: QtBot, value: Decimal, semantic: str
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    page.show_baskets(history_result(net_realized_pnl=value))

    assert semantic in page.total_net_pnl.text()


def test_basket_refresh_loading_keeps_last_known_durable_result(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    basket = basket_result()
    page.show_baskets(history_result(basket))

    page.set_baskets_loading(True)

    assert page.basket_table.rowCount() == 1
    assert page.basket_state.text() == "Loading trade history…"
    assert not page.apply_button.isEnabled()
    assert not page.reset_button.isEnabled()
    assert page.total_net_pnl.text() == "19.58 USDT · Profit"
    assert not page.total_net_pnl.isHidden()

    page.set_baskets_loading(False)
    assert page.apply_button.isEnabled()
    assert page.reset_button.isEnabled()


def test_empty_basket_result_preserves_the_exact_query_summary(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    exact_empty_result = history_result(
        net_realized_pnl=Decimal("0.000000000000000001"),
    )

    page.show_baskets_empty(exact_empty_result)

    assert page.basket_state.text() == "No Basket history"
    assert page.total_net_pnl.text() == "0.000000000000000001 USDT · Profit"
    assert page.total_items.text() == "0 total Baskets"
    assert not page.total_net_pnl.isHidden()
    assert page.basket_table.rowCount() == 0


def test_empty_basket_history_state_is_centered_in_table_body(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    page.resize(1400, 1000)
    qtbot.addWidget(page)
    page.show()

    page.show_baskets_empty(history_result())

    qtbot.waitUntil(page.basket_empty_state.isVisible)
    assert (
        page.basket_empty_state.geometry().center()
        == page.basket_table.viewport().rect().center()
    )
    assert page.basket_state.isHidden()

    page.show_baskets(history_result(basket_result()))

    assert page.basket_empty_state.isHidden()


def test_initial_basket_failure_clears_only_trade_history_scope(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()

    page.show_baskets_unavailable("Trade History unavailable")

    assert page.basket_table.rowCount() == 0
    assert page.fill_table.rowCount() == 0
    assert page.total_net_pnl.isHidden()
    assert page.total_net_pnl_label.isHidden()
    assert page.basket_state.text() == "Trade History unavailable"
    assert page.retry_baskets_button.isVisible()
    assert not page.retry_fills_button.isVisible()


def test_basket_refresh_failure_keeps_last_known_durable_result(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    basket = basket_result()
    page.show_baskets(history_result(basket))

    page.set_baskets_loading(True)
    page.show_baskets_unavailable("Trade History unavailable")

    assert page.basket_table.rowCount() == 1
    assert page.total_net_pnl.text() == "19.58 USDT · Profit"
    assert not page.total_net_pnl.isHidden()
    assert page.total_items.text() == "1 total Baskets"
    assert page.basket_state.text() == "Stale · Trade History unavailable"
    assert page.retry_baskets_button.isVisible()
    assert not page.retry_fills_button.isVisible()


def test_empty_trade_fills_state_is_centered_in_table_body(qtbot: QtBot) -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )
    page = TradeHistoryPage()
    page.resize(1400, 1000)
    qtbot.addWidget(page)
    page.show()
    page.history_tabs.setCurrentIndex(1)

    page.show_trades_empty()

    qtbot.waitUntil(page.fill_empty_state.isVisible)
    assert (
        page.fill_empty_state.geometry().center()
        == page.fill_table.viewport().rect().center()
    )
    assert page.fill_state.isHidden()

    page.show_trades((item,))

    assert page.fill_empty_state.isHidden()


def test_retry_buttons_and_filter_error_are_scoped(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    basket_retries: list[None] = []
    fill_retries: list[None] = []
    page.baskets_retry_requested.connect(lambda: basket_retries.append(None))
    page.fills_retry_requested.connect(lambda: fill_retries.append(None))

    page.show_filter_error("From Date must not be after To Date")
    assert page.filter_error.text() == "From Date must not be after To Date"

    page.show_baskets_unavailable("Trade History unavailable")
    click(page.retry_baskets_button)
    page.show_baskets(history_result(basket_result()))
    page.history_tabs.setCurrentIndex(1)
    page.show_trades_unavailable("Trade History unavailable")
    click(page.retry_fills_button)

    assert basket_retries == [None]
    assert fill_retries == [None]
