from decimal import Decimal
from functools import partial

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QTableWidget
from pytestqt.qtbot import QtBot

from tests.support.trade_history_records import basket_result
from tiewtrade.application.trade_history import BasketHistoryResult
from tiewtrade.ui.trading_workspace import TradingWorkspace, WorkspaceTab


def test_trading_tables_are_named_and_scroll_horizontally(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(760, 700)
    workspace.show()
    workspace.trade_history.show_baskets(
        BasketHistoryResult(
            items=(
                basket_result(
                    realized_pnl=Decimal("-1.25"),
                ),
            ),
            realized_pnl=Decimal("-1.25"),
        )
    )

    tables = (
        workspace.position_basket.table,
        workspace.trade_history.basket_table,
        workspace.trade_history.fill_table,
    )
    for table in tables:
        assert table.accessibleName()
        assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        for column in range(table.columnCount()):
            table.setColumnWidth(column, 180)
        table.resize(360, 180)
        table.show()
        qtbot.waitUntil(partial(_has_horizontal_overflow, table))


def _has_horizontal_overflow(table: QTableWidget) -> bool:
    return table.horizontalScrollBar().maximum() > 0


def test_the_history_filter_bar_fits_the_narrowest_supported_window(
    qtbot: QtBot,
) -> None:
    """At 1024px the bar shares the row with a 360px Bot Control panel."""
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1024, 700)
    workspace.show()
    workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    page = workspace.trade_history
    # Too narrow for one row beside the Bot Control, so the bar folds in two.
    qtbot.waitUntil(lambda: page.basket_filters.line_count() == 2)
    qtbot.waitUntil(
        lambda: page.basket_filters.height() >= page.basket_filters.sizeHint().height()
    )

    # A chip's own label is its period, so it names the window it stands for.
    for chip in page.basket_filters.range_chips.values():
        assert chip.accessibleName().startswith("Last ")
    assert page.basket_filters.from_date.accessibleName() == "From Date"
    assert page.basket_filters.to_date.accessibleName() == "To Date"

    bar = page.basket_filters.rect()
    for control in (
        *page.basket_filters.range_chips.values(),
        page.basket_filters.from_date,
        page.basket_filters.to_date,
        page.basket_filters.search_button,
        page.basket_filters.reset_button,
    ):
        assert bar.contains(
            QRect(control.mapTo(page.basket_filters, QPoint(0, 0)), control.size())
        )
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
