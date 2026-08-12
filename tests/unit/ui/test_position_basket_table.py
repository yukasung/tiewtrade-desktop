from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidget
from pytestqt.qtbot import QtBot

from tests.support.workspace_snapshots import basket_snapshot
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    empty_position_basket_tab,
    failed_position_basket_tab,
    loading_position_basket_tab,
    ready_position_basket_tab,
    stale_position_basket_tab,
)
from tiewtrade.ui.position_basket_table import PositionBasketTable
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR

OBSERVED_AT = datetime(2026, 8, 2, 1, 2, 4, tzinfo=UTC)


def _futures_basket() -> BasketSnapshot:
    return BasketSnapshot(
        symbol="BTCUSDT",
        market_type="futures",
        entry_count=2,
        total_quantity=Decimal("0.00600000"),
        average_entry_price=Decimal("66000.1250"),
        current_price=Decimal("66321.1200"),
        take_profit_price=Decimal("67000.0000"),
        unrealized_pnl=Decimal("1.92600000"),
        liquidation_price=Decimal("44000.5000"),
        entry_gate=BasketEntryGate.OPEN,
        cooldown_until=None,
        updated_at_utc=OBSERVED_AT,
    )


def _row(table: QTableWidget, row: int) -> tuple[str, ...]:
    values: list[str] = []
    for column in range(table.columnCount()):
        item = table.item(row, column)
        assert item is not None
        values.append(item.text())
    return tuple(values)


def test_position_basket_renders_application_facts_without_recalculation(
    qtbot: QtBot,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert widget.headers == (
        "Symbol",
        "Market Type",
        "Entry Count",
        "Total Quantity",
        "Average Entry Price",
        "Basket Take Profit",
        "Unrealized PnL",
        "Basket Lifecycle",
    )
    assert _row(widget.table, 0) == (
        "BTCUSDT",
        "Futures",
        "2",
        "0.00600000",
        "66000.1250",
        "67000.0000",
        "1.93 USDT",
        "Active",
    )
    assert widget.state_label.text() == ""
    assert widget.state_label.accessibleName() == "Position / Basket status"


def test_columns_align_numbers_right_and_short_labels_centre(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    centre = Qt.AlignmentFlag.AlignCenter
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

    widget.show_snapshot(
        ready_position_basket_tab(basket_snapshot(), observed_at_utc=OBSERVED_AT)
    )

    assert widget.ALIGNMENTS == (
        left,
        centre,
        centre,
        right,
        right,
        right,
        right,
        left,
    )
    for column, alignment in enumerate(widget.ALIGNMENTS):
        cell = widget.table.item(0, column)
        header = widget.table.horizontalHeaderItem(column)
        assert cell is not None and header is not None
        # A header out of step with its column reads as a different column.
        assert cell.textAlignment() == alignment
        assert header.textAlignment() == alignment


@pytest.mark.parametrize(
    ("unrealized_pnl", "expected_text", "expected_colour"),
    [
        pytest.param(Decimal("1.926"), "1.93 USDT", POSITIVE_COLOR, id="profit"),
        pytest.param(Decimal("-0.15132"), "-0.15 USDT", NEGATIVE_COLOR, id="loss"),
        pytest.param(Decimal("0"), "0.00 USDT", None, id="break-even"),
    ],
)
def test_unrealized_pnl_reads_as_a_two_decimal_figure_coloured_by_its_sign(
    qtbot: QtBot,
    unrealized_pnl: Decimal,
    expected_text: str,
    expected_colour: str | None,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(unrealized_pnl=unrealized_pnl),
            observed_at_utc=OBSERVED_AT,
        )
    )

    pnl = widget.table.item(0, 6)
    assert pnl is not None
    assert pnl.text() == expected_text
    # A break-even figure keeps the table's own colour rather than picking a side.
    plain = widget.table.item(0, 0)
    assert plain is not None
    expected_brush = (
        plain.foreground()
        if expected_colour is None
        else QBrush(QColor(expected_colour))
    )
    assert pnl.foreground() == expected_brush


@pytest.mark.parametrize(
    ("basket", "expected"),
    [
        pytest.param(
            basket_snapshot(
                entry_gate=BasketEntryGate.COOLDOWN, cooldown_until=date(2026, 3, 1)
            ),
            "Cooldown until Mar 2026",
            id="cooldown",
        ),
        pytest.param(
            basket_snapshot(entry_gate=BasketEntryGate.MAX_ENTRIES_REACHED),
            "Max Entries reached",
            id="max-entries",
        ),
    ],
)
def test_blocked_entries_read_as_their_reason(
    qtbot: QtBot,
    basket: BasketSnapshot,
    expected: str,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(ready_position_basket_tab(basket, observed_at_utc=OBSERVED_AT))

    gate = widget.table.item(0, 7)
    assert gate is not None
    assert gate.text() == expected


def test_position_basket_states_preserve_or_clear_application_facts(
    qtbot: QtBot,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    ready = ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)

    for snapshot, expected_state in (
        (loading_position_basket_tab(ready), "Loading Position / Basket…"),
        (
            failed_position_basket_tab(ready, "Position / Basket data is unavailable"),
            "Position / Basket data is unavailable",
        ),
        (
            stale_position_basket_tab(ready),
            "Stale Position / Basket data as of 2026-08-02 08:02:04",
        ),
    ):
        widget.show_snapshot(snapshot)
        assert widget.table.rowCount() == 1
        assert widget.state_label.text() == expected_state
        assert not widget.state_label.isHidden()

    widget.show_snapshot(empty_position_basket_tab())

    assert widget.table.rowCount() == 0
    assert widget.state_label.text() == "No open Position or Basket"


def test_empty_position_basket_state_is_centered_in_table_body(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    widget.resize(1200, 360)
    qtbot.addWidget(widget)
    widget.show()

    widget.show_snapshot(empty_position_basket_tab())

    qtbot.waitUntil(widget.empty_state.isVisible)
    assert (
        widget.empty_state.geometry().center()
        == widget.table.viewport().rect().center()
    )
    assert widget.state_label.isHidden()

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert widget.empty_state.isHidden()


def test_position_basket_ignores_wrong_snapshot_type(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    widget.show_snapshot(object())

    assert widget.table.rowCount() == 1
    assert widget.table.accessibleName() == "Position / Basket"
