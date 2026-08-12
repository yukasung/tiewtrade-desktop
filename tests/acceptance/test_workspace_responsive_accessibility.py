from decimal import Decimal
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget
from pytestqt.qtbot import QtBot

from tests.support.trade_history_records import basket_result
from tiewtrade.application.trade_history import BasketHistoryResult
from tiewtrade.ui.trading_workspace import TradingWorkspace


def test_trading_tables_are_named_and_scroll_horizontally(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(760, 700)
    workspace.show()
    workspace.trade_history.show_baskets(
        BasketHistoryResult(
            items=(
                basket_result(
                    gross_realized_pnl=Decimal("-1"),
                    trading_fees=Decimal("0.25"),
                    net_realized_pnl=Decimal("-1.25"),
                ),
            ),
            net_realized_pnl=Decimal("-1.25"),
        )
    )

    assert workspace.trade_history.total_net_pnl.text() == "-1.25 USDT"

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
