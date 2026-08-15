from datetime import date, timedelta
from decimal import Decimal
from time import perf_counter

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click, qdate, table_item
from tests.support.qt_rendering import ink_colours
from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.ui.display_time import display_today
from tiewtrade.ui.table_column import Column
from tiewtrade.ui.theme import DARK_THEME, NEGATIVE_COLOR, POSITIVE_COLOR
from tiewtrade.ui.trade_history_page import TradeHistoryPage
from tiewtrade.ui.trade_history_presenter import (
    BASKET_COLUMNS,
    FILL_COLUMNS,
    QuickRange,
    TradeHistoryFilterValues,
)
from tiewtrade.ui.trade_history_workflow import HistoryTab


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


def cell(
    table: QTableWidget, headers: tuple[str, ...], row: int, header: str
) -> QTableWidgetItem:
    """A cell read by its heading, so a moved column cannot pass by luck."""
    return table_item(table, row, headers.index(header))


def assert_headers_match[Record](
    table: QTableWidget, columns: tuple[Column[Record], ...]
) -> None:
    assert table.columnCount() == len(columns)
    for index, column in enumerate(columns):
        header = table.horizontalHeaderItem(index)
        body = table.item(0, index)
        assert header is not None and body is not None
        assert header.text() == column.header
        assert header.textAlignment() == column.alignment
        assert body.textAlignment() == column.alignment


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
        "Total",
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


def test_the_filter_bar_offers_quick_ranges_and_an_open_ended_date_range(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    assert [chip.text() for chip in page.range_chips.values()] == [
        "1 Day",
        "1 Week",
        "1 Month",
        "3 Months",
    ]
    assert [chip.accessibleName() for chip in page.range_chips.values()] == [
        "Last 1 Day",
        "Last 1 Week",
        "Last 1 Month",
        "Last 3 Months",
    ]
    assert not any(chip.isChecked() for chip in page.range_chips.values())
    assert page.from_date.accessibleName() == "From Date"
    assert page.to_date.accessibleName() == "To Date"
    # Nothing is picked to begin with, so the range is open at both ends.
    assert page.filter_values() == TradeHistoryFilterValues()
    assert page.from_date.specialValueText() == "YYYY-MM-DD"
    assert page.to_date.specialValueText() == "YYYY-MM-DD"


def test_showing_the_trade_half_swaps_only_which_table_is_read(
    qtbot: QtBot,
) -> None:
    """The tab strip is the workspace's; the page only answers which half."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()

    assert page.basket_table.isVisible()

    page.show_history_tab(HistoryTab.TRADES)

    assert page.fill_table.isVisible()
    assert not page.basket_table.isVisible()
    # One filter bar serves both halves, so it does not change with them.
    assert page.search_button.isVisible()
    assert page.from_date.isVisible()


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
    page.show_history_tab(HistoryTab.TRADES)
    page.show_trades((item,))

    assert page.total_items.text() == "1 total Baskets"
    assert page.fill_table.rowCount() == 1
    assert cell(page.fill_table, page.fill_headers, 0, "Symbol").text() == "ETHUSDT"
    assert cell(page.fill_table, page.fill_headers, 0, "Total").text().endswith(" USDT")
    # The colour follows the side itself, not the word the column prints for it.
    assert cell(page.fill_table, page.fill_headers, 0, "Side").foreground() == QBrush(
        QColor(POSITIVE_COLOR)
    )
    assert cell(
        page.basket_table, page.basket_headers, 0, "Net PnL"
    ).foreground() == QBrush(QColor(POSITIVE_COLOR))

    page.show_history_tab(HistoryTab.BASKETS)
    page.show_baskets(BasketHistoryResult((basket,), Decimal("19.58")))
    page.show_history_tab(HistoryTab.TRADES)

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
    assert not page.search_button.isEnabled()
    # A chip searches on the spot, so it is held back with the button.
    assert not page.range_chips[QuickRange.ONE_WEEK].isEnabled()

    page.show_history_tab(HistoryTab.TRADES)

    assert page.search_button.isEnabled()
    assert page.range_chips[QuickRange.ONE_WEEK].isEnabled()
    page.set_trades_loading(True)
    assert not page.search_button.isEnabled()


def test_page_scrolls_vertically_without_squashing_filter_controls(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    # Short enough that the tables cannot fit, which is what is scrolled to.
    page.setFixedSize(1200, 140)
    page.show()

    vertical = page.scroll_area.verticalScrollBar()
    horizontal = page.scroll_area.horizontalScrollBar()
    qtbot.waitUntil(lambda: vertical.maximum() > 0)

    controls = (
        *page.range_chips.values(),
        page.from_date,
        page.to_date,
        page.reset_button,
        page.search_button,
    )
    control_rects = [
        widget_rect_in_parent(control, page.filter_bar) for control in controls
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
    # Short enough that the tables cannot fit, which is what is scrolled to.
    page.setFixedSize(1200, 140)
    page.show()

    scroll_bar = page.scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0)
    page.reset_button.setFocus()
    qtbot.waitUntil(page.reset_button.hasFocus)

    QTest.keyClick(page.reset_button, Qt.Key.Key_Tab)

    assert page.basket_table.hasFocus()
    qtbot.waitUntil(lambda: scroll_bar.value() > 0)

    assert not page.scroll_area.hasFocus()


def test_the_filter_row_reads_left_to_right_in_the_order_it_is_used(
    qtbot: QtBot,
) -> None:
    """Ranges, then the dates they fill, then the actions, then the table."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1200, 600)
    page.show()

    last_chip = page.range_chips[QuickRange.THREE_MONTHS]
    last_chip.setFocus()
    qtbot.waitUntil(last_chip.hasFocus)

    # A date field holds its own sections, so it is entered and left as one.
    QTest.keyClick(last_chip, Qt.Key.Key_Tab)
    assert page.from_date.hasFocus()

    page.search_button.setFocus()
    QTest.keyClick(page.search_button, Qt.Key.Key_Tab)
    assert page.reset_button.hasFocus()


def test_apply_and_reset_emit_immutable_filter_values(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    emitted: list[TradeHistoryFilterValues] = []
    resets: list[None] = []
    page.apply_filters_requested.connect(emitted.append)
    page.reset_requested.connect(lambda: resets.append(None))
    page.from_date.setDate(qdate(date(2026, 1, 2)))
    page.to_date.setDate(qdate(date(2026, 1, 3)))

    click(page.search_button)
    picked = TradeHistoryFilterValues(
        from_date=date(2026, 1, 2), to_date=date(2026, 1, 3)
    )
    assert emitted == [picked]

    click(page.reset_button)
    assert resets == [None]
    assert emitted == [picked]
    assert page.filter_values() == TradeHistoryFilterValues()


def test_an_emptied_date_leaves_that_end_of_the_range_open(qtbot: QtBot) -> None:
    """No checkbox guards the dates any more: blank is how a bound is dropped."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    page.from_date.setDate(qdate(date(2026, 1, 2)))
    page.to_date.setDate(qdate(date(2026, 1, 3)))

    page.to_date.setFocus()
    QTest.keyClick(page.to_date, Qt.Key.Key_Delete)

    assert page.to_date.text() == "YYYY-MM-DD"
    assert page.filter_values() == TradeHistoryFilterValues(from_date=date(2026, 1, 2))


def test_a_quick_range_fills_both_dates_and_searches_at_once(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    emitted: list[TradeHistoryFilterValues] = []
    page.apply_filters_requested.connect(emitted.append)
    today = display_today()

    click(page.range_chips[QuickRange.ONE_WEEK])

    assert emitted == [
        TradeHistoryFilterValues(from_date=today - timedelta(days=6), to_date=today)
    ]
    assert page.range_chips[QuickRange.ONE_WEEK].isChecked()


def test_only_one_quick_range_is_in_force_and_a_typed_date_replaces_it(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    click(page.range_chips[QuickRange.ONE_WEEK])
    click(page.range_chips[QuickRange.ONE_MONTH])

    assert not page.range_chips[QuickRange.ONE_WEEK].isChecked()
    assert page.range_chips[QuickRange.ONE_MONTH].isChecked()

    page.from_date.setDate(qdate(date(2026, 1, 2)))

    assert not any(chip.isChecked() for chip in page.range_chips.values())

    click(page.range_chips[QuickRange.ONE_DAY])
    click(page.reset_button)

    assert not any(chip.isChecked() for chip in page.range_chips.values())
    assert page.filter_values() == TradeHistoryFilterValues()


def test_the_filter_row_folds_instead_of_clipping_at_the_narrowest_window(
    qtbot: QtBot,
) -> None:
    """The scroll area has no horizontal bar, so an overflowing row is lost."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1004, 600)
    page.show()
    qtbot.waitUntil(lambda: page.filter_bar.width() > 0)

    assert page.filter_bar.line_count() == 1

    page.setFixedSize(588, 600)
    qtbot.waitUntil(lambda: page.filter_bar.line_count() > 1)

    bar = page.filter_bar.rect()
    for control in (
        *page.range_chips.values(),
        page.from_date,
        page.to_date,
        page.search_button,
        page.reset_button,
    ):
        assert bar.contains(widget_rect_in_parent(control, page.filter_bar))
    assert page.scroll_area.horizontalScrollBar().maximum() == 0


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
    ("value", "expected_text", "expected_colour"),
    [
        pytest.param(Decimal("1"), "1.00 USDT", POSITIVE_COLOR, id="profit"),
        pytest.param(Decimal("-1"), "-1.00 USDT", NEGATIVE_COLOR, id="loss"),
        pytest.param(Decimal("0"), "0.00 USDT", None, id="break-even"),
    ],
)
def test_the_summary_is_painted_by_its_sign_rather_than_labelled(
    qtbot: QtBot,
    value: Decimal,
    expected_text: str,
    expected_colour: str | None,
) -> None:
    """The figure says the amount and the colour says the side, so no word does.

    Read off the rendered pixels: the theme paints this label by object name,
    which outranks anything a palette or a property could ask for.
    """
    page = TradeHistoryPage()
    page.setStyleSheet(DARK_THEME)
    qtbot.addWidget(page)
    page.show()

    page.show_baskets(history_result(net_realized_pnl=value))

    assert page.total_net_pnl.text() == expected_text
    inks = ink_colours(page.total_net_pnl)
    if expected_colour is None:
        assert POSITIVE_COLOR.lower() not in inks
        assert NEGATIVE_COLOR.lower() not in inks
    else:
        assert expected_colour.lower() in inks


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
    assert not page.search_button.isEnabled()
    assert not page.reset_button.isEnabled()
    assert page.total_net_pnl.text() == "19.58 USDT"
    assert not page.total_net_pnl.isHidden()

    page.set_baskets_loading(False)
    assert page.search_button.isEnabled()
    assert page.reset_button.isEnabled()


def test_empty_basket_result_still_carries_its_summary_to_the_screen(
    qtbot: QtBot,
) -> None:
    """No rows is a result, not a blank: its summary is shown, not cleared."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    # The smallest amount that still reads on screen, so a summary quietly
    # dropped cannot pass as one that was merely too small to see.
    empty_result = history_result(net_realized_pnl=Decimal("0.01"))

    page.show_baskets_empty(empty_result)

    assert page.basket_state.text() == "No Basket history"
    assert page.total_net_pnl.text() == "0.01 USDT"
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
    assert page.total_net_pnl.text() == "19.58 USDT"
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
    page.show_history_tab(HistoryTab.TRADES)

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
    page.show_history_tab(HistoryTab.TRADES)
    page.show_trades_unavailable("Trade History unavailable")
    click(page.retry_fills_button)

    assert basket_retries == [None]
    assert fill_retries == [None]


def test_headers_stay_in_step_with_the_columns_they_name(qtbot: QtBot) -> None:
    """A heading out of step with its column reads as a different column."""
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )
    page = TradeHistoryPage()
    qtbot.addWidget(page)

    page.show_baskets(history_result(basket_result()))
    page.show_trades((item,))

    assert_headers_match(page.basket_table, BASKET_COLUMNS)
    assert_headers_match(page.fill_table, FILL_COLUMNS)


def test_a_refresh_measures_the_columns_once_instead_of_per_cell(
    qtbot: QtBot,
) -> None:
    """ResizeToContents re-reads every row per cell written, which froze the app."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1200, 600)
    page.show()

    page.show_baskets(history_result(*(basket_result() for _ in range(20))))

    header = page.basket_table.horizontalHeader()
    assert header.sectionResizeMode(0) != QHeaderView.ResizeMode.ResizeToContents
    # Measured once all the same, so the columns still fit what they hold.
    for column in range(page.basket_table.columnCount() - 1):
        assert page.basket_table.columnWidth(column) > 0


def test_a_second_refresh_costs_no_more_than_the_first(qtbot: QtBot) -> None:
    """Overwriting rows in place is the case that used to take seconds."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1200, 600)
    page.show()
    result = history_result(*(basket_result() for _ in range(300)))

    page.show_baskets(result)
    started = perf_counter()
    page.show_baskets(result)
    elapsed = perf_counter() - started

    # A generous budget: the failure this guards against was ~1000x over it.
    assert elapsed < 2.0
    assert page.basket_table.rowCount() == 300


def test_a_basket_result_does_not_change_which_half_is_read(qtbot: QtBot) -> None:
    """The tab strip chose the half; a reply landing later cannot re-choose."""
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.show()
    page.show_history_tab(HistoryTab.TRADES)

    page.set_baskets_loading(True)
    page.show_baskets(history_result(basket_result()))
    page.show_baskets_unavailable("Basket History unavailable")

    assert page.fill_table.isVisible()
    assert not page.basket_table.isVisible()
