from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem
from pytestqt.qtbot import QtBot

from tests.support.qt_rendering import ink_colours
from tests.support.workspace_snapshots import basket_entries, basket_snapshot
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketEntrySnapshot,
    BasketSnapshot,
    empty_position_basket_tab,
    failed_position_basket_tab,
    loading_position_basket_tab,
    ready_position_basket_tab,
    stale_position_basket_tab,
)
from tiewtrade.ui.position_basket_table import PositionBasketTable
from tiewtrade.ui.theme import DARK_THEME, NEGATIVE_COLOR, POSITIVE_COLOR

OBSERVED_AT = datetime(2026, 8, 2, 1, 2, 4, tzinfo=UTC)


def _entry(
    number: int,
    price: str,
    unrealized_pnl: str,
    take_profit_price: str | None = None,
) -> BasketEntrySnapshot:
    return BasketEntrySnapshot(
        entry_number=number,
        filled_at_utc=datetime(2026, 8, 2, number, 0, tzinfo=UTC),
        price=Decimal(price),
        quantity=Decimal("0.00300000"),
        notional=Decimal(price) * Decimal("0.00300000"),
        unrealized_pnl=Decimal(unrealized_pnl),
        take_profit_price=(
            Decimal(price) + Decimal("1000")
            if take_profit_price is None
            else Decimal(take_profit_price)
        ),
    )


def _futures_basket() -> BasketSnapshot:
    """Two Entries at different prices: the case an average alone cannot show."""
    return BasketSnapshot(
        symbol="BTCUSDT",
        market_type="futures",
        entry_count=2,
        entries=(
            _entry(1, "66000.0000", "0.96336", take_profit_price="67000.0000"),
            _entry(2, "66000.2500", "0.96264", take_profit_price="67000.2500"),
        ),
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


def _row(widget: PositionBasketTable, row: int = 0) -> dict[str, str]:
    """A row read by heading, so a moved column cannot pass by luck."""
    values: dict[str, str] = {}
    for column, header in enumerate(widget.headers):
        item = widget.table.item(row, column)
        assert item is not None
        values[header] = item.text()
    return values


def _cell(widget: PositionBasketTable, header: str, row: int = 0) -> QTableWidgetItem:
    item = widget.table.item(row, widget.headers.index(header))
    assert item is not None
    return item


def test_each_entry_of_the_basket_is_its_own_row(qtbot: QtBot) -> None:
    """The prices actually paid, not the average that hides them."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert widget.headers == (
        "Entry",
        "Filled At",
        "Entry Price",
        "Take Profit",
        "Quantity",
        "Total",
        "Unrealized PnL",
        "Basket Lifecycle",
    )
    assert widget.table.rowCount() == 2
    assert _row(widget, 0) == {
        "Entry": "#1",
        "Filled At": "2026-08-02 08:00:00",
        "Entry Price": "66,000.0000",
        "Take Profit": "67,000.0000",
        "Quantity": "0.00300000",
        "Total": "198.00 USDT",
        "Unrealized PnL": "0.96 USDT",
        "Basket Lifecycle": "Active",
    }
    assert _row(widget, 1)["Entry"] == "#2"
    assert _row(widget, 1)["Entry Price"] == "66,000.2500"
    assert _row(widget, 1)["Filled At"] == "2026-08-02 09:00:00"
    assert widget.state_label.text() == ""
    assert widget.state_label.accessibleName() == "Position / Basket status"


def test_every_entry_reads_the_target_it_is_sold_at(qtbot: QtBot) -> None:
    """Entries bought at different prices are sold at different targets."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert _cell(widget, "Take Profit", 0).text() == "67,000.0000"
    assert _cell(widget, "Take Profit", 1).text() == "67,000.2500"
    for row in (0, 1):
        # The lifecycle is the Basket's, so it does read the same on both.
        assert _cell(widget, "Basket Lifecycle", row).text() == "Active"


def test_money_is_grouped_and_a_quantity_is_not(qtbot: QtBot) -> None:
    """Thousands read at a glance; an amount of the asset has no thousands."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    row = _row(widget, 0)
    assert row["Entry Price"] == "66,000.0000"
    assert row["Take Profit"] == "67,000.0000"
    assert row["Total"] == "198.00 USDT"
    # The quantity is counted in BTC, where a separator would suggest a
    # scale the number does not have.
    assert row["Quantity"] == "0.00300000"


def test_only_the_total_frames_the_entries(qtbot: QtBot) -> None:
    """The sum is the one fact no row carries; the rest of the tab is Entries.

    The symbol and market are named by the chart and the session panel, so the
    table does not spend a line repeating them.
    """
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert widget.total_label.text() == (
        "2 Entries · Total 0.00600000 · Average 66,000.1250 · Unrealized 1.93 USDT"
    )
    assert widget.total_label.accessibleName() == "Basket total"
    assert not hasattr(widget, "basket_label")


def test_a_single_entry_basket_reads_as_one_entry(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_count=1, entries=basket_entries("64000")),
            observed_at_utc=OBSERVED_AT,
        )
    )

    assert widget.table.rowCount() == 1
    assert widget.total_label.text().startswith("1 Entry · ")


def test_a_moved_column_carries_its_own_reading(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Values follow their heading, so reordering cannot silently swap cells."""
    reversed_columns = tuple(reversed(PositionBasketTable.COLUMNS))
    monkeypatch.setattr(PositionBasketTable, "COLUMNS", reversed_columns)
    monkeypatch.setattr(
        PositionBasketTable,
        "HEADERS",
        tuple(column.header for column in reversed_columns),
    )
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert widget.headers[0] == "Basket Lifecycle"
    assert _row(widget)["Entry"] == "#1"
    assert _row(widget)["Unrealized PnL"] == "0.96 USDT"
    assert _cell(widget, "Unrealized PnL").foreground() == QBrush(
        QColor(POSITIVE_COLOR)
    )


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
        centre,
        left,
        right,
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
def test_entry_pnl_reads_as_a_two_decimal_figure_coloured_by_its_sign(
    qtbot: QtBot,
    unrealized_pnl: Decimal,
    expected_text: str,
    expected_colour: str | None,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(
                entry_count=1,
                entries=(_entry(1, "64000", str(unrealized_pnl)),),
            ),
            observed_at_utc=OBSERVED_AT,
        )
    )

    pnl = _cell(widget, "Unrealized PnL")
    assert pnl.text() == expected_text
    # A break-even figure keeps the table's own colour rather than picking a side.
    plain = _cell(widget, "Entry")
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

    # A blocked Basket says so on every Entry, not only on the row last taken.
    for row in range(widget.table.rowCount()):
        assert _cell(widget, "Basket Lifecycle", row).text() == expected


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
        assert widget.table.rowCount() == 2
        assert widget.state_label.text() == expected_state
        assert not widget.state_label.isHidden()

    widget.show_snapshot(empty_position_basket_tab())

    assert widget.table.rowCount() == 0
    assert widget.state_label.text() == "No open Position or Basket"
    assert widget.total_label.isHidden()


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

    assert widget.table.rowCount() == 2
    assert widget.table.accessibleName() == "Position / Basket"


@pytest.mark.parametrize(
    ("unrealized_pnl", "expected_colour"),
    [
        pytest.param(Decimal("1.926"), POSITIVE_COLOR, id="profit"),
        pytest.param(Decimal("-0.15132"), NEGATIVE_COLOR, id="loss"),
        pytest.param(Decimal("0"), None, id="break-even"),
    ],
)
def test_the_total_is_painted_by_its_sign(
    qtbot: QtBot, unrealized_pnl: Decimal, expected_colour: str | None
) -> None:
    """The same reading the Trade History summary gives, given the same way."""
    widget = PositionBasketTable()
    widget.setStyleSheet(DARK_THEME)
    qtbot.addWidget(widget)
    widget.show()

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(unrealized_pnl=unrealized_pnl),
            observed_at_utc=OBSERVED_AT,
        )
    )

    inks = ink_colours(widget.total_label)
    if expected_colour is None:
        assert POSITIVE_COLOR.lower() not in inks
        assert NEGATIVE_COLOR.lower() not in inks
    else:
        assert expected_colour.lower() in inks
