from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QWidget
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click, qdate, table_item
from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    BasketHistoryPage,
    BasketHistoryResult,
    TradeFillHistoryItem,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.ui.trade_history_page import TradeHistoryPage
from tiewtrade.ui.trade_history_presenter import TradeHistoryFilterValues


def history_page(
    *items: object,
    page: int = 1,
    total_items: int | None = None,
    net_realized_pnl: Decimal = Decimal("19.58"),
) -> BasketHistoryPage:
    return BasketHistoryPage(
        items=items,  # type: ignore[arg-type]
        page=page,
        page_size=50,
        total_items=len(items) if total_items is None else total_items,
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
    assert page.previous_button.isHidden()
    assert page.next_button.isHidden()
    assert page.page_label.isHidden()

    page.history_tabs.setCurrentIndex(1)

    assert page.status.isHidden()
    assert page.status_label.isHidden()
    assert page.fill_table.isVisible()
    assert not page.basket_table.isVisible()


def test_modern_basket_and_cross_basket_trade_results_render_independently(
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


def test_show_baskets_selects_first_row_without_emitting_selection(
    qtbot: QtBot,
) -> None:
    first = basket_result()
    second = basket_result(basket_id=UUID("00000000-0000-0000-0000-000000000202"))
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    selected: list[UUID] = []
    page.basket_selected.connect(selected.append)

    page.show_baskets(history_page(first, second, net_realized_pnl=Decimal("39.16")))

    assert page.basket_table.currentRow() == 0
    assert (
        table_item(page.basket_table, 0, 0).data(Qt.ItemDataRole.UserRole)
        == first.basket_id
    )
    assert selected == []

    page.basket_table.selectRow(1)
    assert selected == [second.basket_id]


def test_tables_are_read_only_row_selection_surfaces(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    for table in (page.basket_table, page.fill_table):
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert (
            table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        )
        assert table.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection
        assert table.accessibleName()


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

    page.show_baskets(history_page(net_realized_pnl=value))

    assert semantic in page.total_net_pnl.text()


def test_basket_pagination_uses_presented_page_state_and_emits_requests(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    requested: list[int] = []
    page.page_requested.connect(requested.append)
    page.show_baskets(history_page(basket_result(), page=2, total_items=120))

    assert page.page_label.text() == "Page 2 of 3"
    assert page.previous_button.isEnabled()
    assert page.next_button.isEnabled()

    click(page.previous_button)
    click(page.next_button)

    assert requested == [1, 3]


def test_basket_refresh_loading_keeps_last_known_durable_result(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    basket = basket_result()
    page.show_baskets(history_page(basket, page=2, total_items=120))
    page.show_fills(basket.basket_id, (trade_fill(),))

    page.set_baskets_loading(True)

    assert page.basket_table.rowCount() == 1
    assert page.fill_table.rowCount() == 1
    assert page.basket_state.text() == "Loading trade history…"
    assert not page.apply_button.isEnabled()
    assert not page.reset_button.isEnabled()
    assert not page.previous_button.isEnabled()
    assert not page.next_button.isEnabled()
    assert page.page_label.text() == "Page 2 of 3"
    assert not page.page_label.isHidden()
    assert page._current_page == 2
    assert page.total_net_pnl.text() == "19.58 USDT · Profit"
    assert not page.total_net_pnl.isHidden()

    page.set_baskets_loading(False)
    assert page.apply_button.isEnabled()
    assert page.reset_button.isEnabled()


def test_empty_basket_result_preserves_exact_query_summary_and_page_state(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    exact_empty_page = history_page(
        page=1,
        total_items=0,
        net_realized_pnl=Decimal("0.000000000000000001"),
    )

    page.show_baskets_empty(exact_empty_page)

    assert page.basket_state.text() == "No trade history"
    assert page.total_net_pnl.text() == "0.000000000000000001 USDT · Profit"
    assert page.total_items.text() == "0 total Baskets"
    assert not page.total_net_pnl.isHidden()
    assert page.page_label.text() == "Page 1 of 1"
    assert page.basket_table.rowCount() == 0
    assert page.fill_table.rowCount() == 0


def test_empty_basket_history_state_is_centered_in_table_body(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    page.resize(1400, 1000)
    qtbot.addWidget(page)
    page.show()

    page.show_baskets_empty(history_page(page=1, total_items=0))

    qtbot.waitUntil(page.basket_empty_state.isVisible)
    assert (
        page.basket_empty_state.geometry().center()
        == page.basket_table.viewport().rect().center()
    )
    assert page.basket_state.isHidden()

    page.show_baskets(history_page(basket_result()))

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
    assert page.page_label.text() == ""
    assert page.page_label.isHidden()
    assert page._current_page == 1
    assert not page.previous_button.isEnabled()
    assert not page.next_button.isEnabled()
    assert page.retry_baskets_button.isVisible()
    assert not page.retry_fills_button.isVisible()


def test_basket_refresh_failure_keeps_last_known_durable_result(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    basket = basket_result()
    page.show_baskets(history_page(basket, page=2, total_items=120))
    page.show_fills(basket.basket_id, (trade_fill(),))

    page.set_baskets_loading(True)
    page.show_baskets_unavailable("Trade History unavailable")

    assert page.basket_table.rowCount() == 1
    assert page.fill_table.rowCount() == 1
    assert page.total_net_pnl.text() == "19.58 USDT · Profit"
    assert not page.total_net_pnl.isHidden()
    assert page.total_items.text() == "120 total Baskets"
    assert page.basket_state.text() == "Stale · Trade History unavailable"
    assert page.page_label.text() == "Page 2 of 3"
    assert page._current_page == 2
    assert page.retry_baskets_button.isVisible()
    assert not page.retry_fills_button.isVisible()


def test_fill_success_and_empty_state_keep_basket_selection(qtbot: QtBot) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show_baskets(history_page(basket))

    page.show_fills(basket.basket_id, (trade_fill(),))
    assert page.fill_table.rowCount() == 1
    assert "Break-even" in table_item(page.fill_table, 0, 7).text()

    page.show_fills_empty(basket.basket_id)
    assert page.basket_table.currentRow() == 0
    assert page.fill_table.rowCount() == 0
    assert page.fill_state.text() == "No fills for this Basket"


def test_empty_trade_fills_state_is_centered_in_table_body(qtbot: QtBot) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    page.resize(1400, 1000)
    qtbot.addWidget(page)
    page.show()
    page.show_baskets(history_page(basket))
    page.history_tabs.setCurrentIndex(1)

    page.show_fills_empty(basket.basket_id)

    qtbot.waitUntil(page.fill_empty_state.isVisible)
    assert (
        page.fill_empty_state.geometry().center()
        == page.fill_table.viewport().rect().center()
    )
    assert page.fill_state.isHidden()

    page.show_fills(basket.basket_id, (trade_fill(),))

    assert page.fill_empty_state.isHidden()


def test_fill_refresh_loading_keeps_same_basket_durable_fills(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    page.show_baskets(history_page(basket))
    page.history_tabs.setCurrentIndex(1)
    page.show_fills(basket.basket_id, (trade_fill(),))
    page.set_fills_loading(True)

    assert page.basket_table.rowCount() == 1
    assert page.basket_table.currentRow() == 0
    assert page.fill_table.rowCount() == 1
    assert page.fill_state.text() == "Loading trade fills…"
    assert page.fill_state.isVisible()
    assert page.retry_fills_button.isHidden()


def test_legacy_fill_rows_cannot_reuse_cross_basket_trade_headers(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    page.show_fills(basket.basket_id, (trade_fill(),))

    side_header = page.fill_table.horizontalHeaderItem(1)
    entry_header = page.fill_table.horizontalHeaderItem(2)
    assert side_header is not None
    assert entry_header is not None
    assert side_header.text() == "Side"
    assert entry_header.text() == "Entry #"

    page.show_trades(())

    symbol_header = page.fill_table.horizontalHeaderItem(1)
    side_header = page.fill_table.horizontalHeaderItem(2)
    assert symbol_header is not None
    assert side_header is not None
    assert symbol_header.text() == "Symbol"
    assert side_header.text() == "Side"


def test_fill_failure_keeps_basket_rows_and_scopes_retry(qtbot: QtBot) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    page.show_baskets(history_page(basket))
    page.history_tabs.setCurrentIndex(1)

    page.show_fills_unavailable(basket.basket_id, "Trade Fills unavailable")

    assert page.basket_table.rowCount() == 1
    assert page.fill_table.rowCount() == 0
    assert page.fill_state.text() == "Trade Fills unavailable"
    assert page.retry_fills_button.isVisible()
    assert not page.retry_baskets_button.isVisible()


def test_fill_refresh_failure_keeps_same_basket_durable_fills(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    page.show_baskets(history_page(basket))
    page.history_tabs.setCurrentIndex(1)
    page.show_fills(basket.basket_id, (trade_fill(),))

    page.show_fills_unavailable(basket.basket_id, "Trade Fills unavailable")

    assert page.basket_table.rowCount() == 1
    assert page.fill_table.rowCount() == 1
    assert page.fill_state.text() == "Stale · Trade Fills unavailable"
    assert page.retry_fills_button.isVisible()
    assert not page.retry_baskets_button.isVisible()


def test_basket_failure_does_not_clobber_existing_fill_failure_state(
    qtbot: QtBot,
) -> None:
    basket = basket_result()
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    page.show_baskets(history_page(basket))
    page.show_fills(basket.basket_id, (trade_fill(),))
    page.show_fills_unavailable(basket.basket_id, "Trade Fills unavailable")

    page.show_baskets_unavailable("Trade History unavailable")

    assert page.basket_state.text() == "Stale · Trade History unavailable"
    assert page.fill_state.text() == "Stale · Trade Fills unavailable"
    assert page.retry_baskets_button.isVisible()
    assert not page.retry_fills_button.isVisible()


def test_fill_loading_for_another_basket_clears_only_fill_scope(qtbot: QtBot) -> None:
    first = basket_result()
    second = basket_result(basket_id=UUID("00000000-0000-0000-0000-000000000202"))
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show_baskets(history_page(first, second))
    page.show_fills(first.basket_id, (trade_fill(),))

    page.basket_table.selectRow(1)
    page.set_fills_loading(True)

    assert page.basket_table.rowCount() == 2
    assert page.basket_table.currentRow() == 1
    assert page.fill_table.rowCount() == 0
    assert page.fill_state.text() == "Loading trade fills…"


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
    page.show_baskets(history_page(basket_result()))
    page.show_fills_unavailable(basket_result().basket_id, "Trade Fills unavailable")
    click(page.retry_fills_button)

    assert basket_retries == [None]
    assert fill_retries == [None]
