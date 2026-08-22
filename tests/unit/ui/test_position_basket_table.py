from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QPushButton, QTableWidgetItem
from pytestqt.qtbot import QtBot

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
from tiewtrade.ui.blank_text import BLANK
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
        entry_gate=BasketEntryGate.OPEN,
        updated_at_utc=OBSERVED_AT,
    )


def _row(widget: PositionBasketTable, row: int = 0) -> dict[str, str]:
    """A row read by heading, so a moved column cannot pass by luck."""
    values: dict[str, str] = {}
    for column, header in enumerate(widget.headers):
        item = widget.table.item(row, column)
        if item is None:
            # The last column holds the order this row can send, not a value.
            assert header == PositionBasketTable.CLOSE_HEADER
            continue
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
        "Date",
        "Entry Price",
        "Take Profit",
        "Quantity",
        "Total",
        "Unrealized P/L",
        "Status",
        "Close Position",
    )
    assert widget.table.rowCount() == 2
    assert _row(widget, 0) == {
        "Date": "2026-08-02 08:00:00",
        "Entry Price": "66,000.0000",
        "Take Profit": "67,000.0000",
        "Quantity": "0.00300000",
        "Total": "198.00 USDT",
        "Unrealized P/L": "0.96 USDT",
        "Status": "Active",
    }
    assert _row(widget, 1)["Entry Price"] == "66,000.2500"
    assert _row(widget, 1)["Date"] == "2026-08-02 09:00:00"
    assert widget.state_label.text() == ""
    assert widget.state_label.accessibleName() == "Position status"


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
        assert _cell(widget, "Status", row).text() == "Active"


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


def test_no_price_column_reads_at_the_basket_level(qtbot: QtBot) -> None:
    """Only the prices the row itself paid and aims at.

    The Basket's average was once restated on every row, where it read as a
    third price beside those two and invited the target to be read as measured
    from it. The target is measured from the Entry's own price, so the average
    is not printed here at all — and nothing takes its place, no summary label
    and no total.
    """
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)
    )

    assert "Average" not in widget.headers
    assert _cell(widget, "Entry Price", 0).text() == "66,000.0000"
    assert _cell(widget, "Entry Price", 1).text() == "66,000.2500"
    assert not hasattr(widget, "total_label")
    assert not hasattr(widget, "basket_label")


def test_a_single_entry_basket_reads_the_one_price_it_paid(
    qtbot: QtBot,
) -> None:
    """One Entry, one price and one target — no averaged figure to compare."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(
                entry_count=1,
                entries=basket_entries("64000"),
                average_entry_price=Decimal("64000"),
            ),
            observed_at_utc=OBSERVED_AT,
        )
    )

    assert widget.table.rowCount() == 1
    assert _cell(widget, "Entry Price", 0).text() == "64,000"
    assert "Average" not in widget.headers


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

    assert widget.headers[0] == "Status"
    assert _row(widget)["Unrealized P/L"] == "0.96 USDT"
    assert _cell(widget, "Unrealized P/L").foreground() == QBrush(
        QColor(POSITIVE_COLOR)
    )


def test_columns_align_numbers_right_and_text_left(qtbot: QtBot) -> None:
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
        # The cell follows its column, so a run of figures shares one right
        # edge. The heading is centred over the column whatever that is.
        assert cell.textAlignment() == alignment
        assert header.textAlignment() == centre


def test_an_unmarked_entry_shows_a_dash_where_its_result_will_be(
    qtbot: QtBot,
) -> None:
    """A Basket read out of the database has no price to mark on yet.

    Everything the database answered is a row already; only the result waits.
    A dash says the question has no answer yet, where 0.00 USDT would say the
    Entry stands exactly where it was bought.
    """
    widget = PositionBasketTable()
    qtbot.addWidget(widget)

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(
                entry_count=1,
                entries=(replace(_entry(1, "64000", "0"), unrealized_pnl=None),),
                current_price=None,
                unrealized_pnl=None,
            ),
            observed_at_utc=OBSERVED_AT,
        )
    )

    row = _row(widget)
    assert row["Unrealized P/L"] == BLANK
    # What needed no price is there in full.
    assert row["Entry Price"] == "64,000"
    assert row["Take Profit"] == "65,000"
    assert row["Total"] == "192.00 USDT"
    # Unmarked is not a sign, so the figure keeps the table's own colour.
    assert _cell(widget, "Unrealized P/L").foreground() == (
        _cell(widget, "Date").foreground()
    )


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

    pnl = _cell(widget, "Unrealized P/L")
    assert pnl.text() == expected_text
    # A break-even figure keeps the table's own colour rather than picking a side.
    plain = _cell(widget, "Date")
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
            basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN),
            "Cooldown",
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
        assert _cell(widget, "Status", row).text() == expected


def test_cancel_cooldown_button_shows_only_during_a_cooldown(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show()

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN),
            observed_at_utc=OBSERVED_AT,
        )
    )
    assert widget.cancel_cooldown_button.isVisible() is True

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_gate=BasketEntryGate.OPEN),
            observed_at_utc=OBSERVED_AT,
        )
    )
    assert widget.cancel_cooldown_button.isVisible() is False


def test_cancel_cooldown_button_emits_a_signal_on_click(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    asked = 0

    def on_clicked() -> None:
        nonlocal asked
        asked += 1

    widget.cancel_cooldown_requested.connect(on_clicked)
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN),
            observed_at_utc=OBSERVED_AT,
        )
    )

    widget.cancel_cooldown_button.click()

    assert asked == 1


def test_cancel_cooldown_button_is_taken_away_while_in_flight(qtbot: QtBot) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show()
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN),
            observed_at_utc=OBSERVED_AT,
        )
    )

    widget.set_cancelling_enabled(False)
    assert widget.cancel_cooldown_button.isEnabled() is False

    widget.set_cancelling_enabled(True)
    assert widget.cancel_cooldown_button.isEnabled() is True


def test_a_cancel_that_failed_is_said_over_the_position_that_is_still_there(
    qtbot: QtBot,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show()
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN),
            observed_at_utc=OBSERVED_AT,
        )
    )

    widget.show_cancel_error("Could not waive the Cooldown.")

    assert widget.state_label.text() == "Could not waive the Cooldown."
    assert not widget.state_label.isHidden()


def test_position_basket_states_preserve_or_clear_application_facts(
    qtbot: QtBot,
) -> None:
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    ready = ready_position_basket_tab(_futures_basket(), observed_at_utc=OBSERVED_AT)

    for snapshot, expected_state in (
        (loading_position_basket_tab(ready), "Loading Position…"),
        (
            failed_position_basket_tab(ready, "Position data is unavailable"),
            "Position data is unavailable",
        ),
        (
            stale_position_basket_tab(ready),
            "Stale Position data as of 2026-08-02 08:02:04",
        ),
    ):
        widget.show_snapshot(snapshot)
        assert widget.table.rowCount() == 2
        assert widget.state_label.text() == expected_state
        assert not widget.state_label.isHidden()

    widget.show_snapshot(empty_position_basket_tab())

    assert widget.table.rowCount() == 0
    assert widget.state_label.text() == "No open Position"


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
    assert widget.table.accessibleName() == "Position"


def _close_button(widget: PositionBasketTable, row: int) -> QPushButton:
    button = widget.close_button(row)
    assert isinstance(button, QPushButton)
    return button


def test_every_entry_carries_the_order_that_closes_it(qtbot: QtBot) -> None:
    """Binance puts the order on the row, not a tick box beside it.

    A checkbox would have the trader select rows and then find the verb
    elsewhere; the button is the order, and it says which one it sends.
    """
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    asked: list[int] = []
    widget.close_entry_requested.connect(asked.append)

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entries=(_entry(1, "100", "1"), _entry(2, "110", "-2"))),
            observed_at_utc=OBSERVED_AT,
        )
    )

    assert _close_button(widget, 0).text() == "Market"
    _close_button(widget, 1).click()

    # The Entry's own number, not the row it happens to sit in: rows are
    # rebuilt on every price, and one of them has already gone before now.
    assert asked == [2]


def test_the_order_is_taken_away_while_one_is_still_in_flight(
    qtbot: QtBot,
) -> None:
    """A sale that has not landed must not be sent twice by an eager hand."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_count=1, entries=(_entry(1, "100", "1"),)),
            observed_at_utc=OBSERVED_AT,
        )
    )

    widget.set_closing_enabled(False)

    assert _close_button(widget, 0).isEnabled() is False
    # A price arrives while the sale is still out, and the rows redraw.
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_count=1, entries=(_entry(1, "100", "2"),)),
            observed_at_utc=OBSERVED_AT,
        )
    )
    assert _close_button(widget, 0).isEnabled() is False

    widget.set_closing_enabled(True)

    assert _close_button(widget, 0).isEnabled() is True


def test_the_order_is_taken_away_while_the_basket_is_in_cooldown(
    qtbot: QtBot,
) -> None:
    """A Cooldown never lapses on its own, so a manual sale must not lift it."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(
                entry_gate=BasketEntryGate.COOLDOWN,
                entry_count=1,
                entries=(_entry(1, "100", "1"),),
            ),
            observed_at_utc=OBSERVED_AT,
        )
    )

    assert _close_button(widget, 0).isEnabled() is False

    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(
                entry_gate=BasketEntryGate.OPEN,
                entry_count=1,
                entries=(_entry(1, "100", "1"),),
            ),
            observed_at_utc=OBSERVED_AT,
        )
    )

    assert _close_button(widget, 0).isEnabled() is True


def test_only_the_pair_that_just_completed_reads_as_cooling_down(
    qtbot: QtBot,
) -> None:
    """Four Entries, two Pairs: only the second Pair is what is cooling down.

    The first Pair's own Cooldown was cancelled long before the second Pair
    could form, so it reads Active — the Status column and the Market button
    must not paint every row with a Cooldown that only the newest Pair holds.
    """
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(
                entry_gate=BasketEntryGate.COOLDOWN,
                completed_pairs=2,
                entry_count=4,
                entries=(
                    _entry(1, "100", "1"),
                    _entry(2, "101", "1"),
                    _entry(3, "102", "1"),
                    _entry(4, "103", "1"),
                ),
            ),
            observed_at_utc=OBSERVED_AT,
        )
    )

    assert [_cell(widget, "Status", row).text() for row in range(4)] == [
        "Active",
        "Active",
        "Cooldown",
        "Cooldown",
    ]
    assert [_close_button(widget, row).isEnabled() for row in range(4)] == [
        True,
        True,
        False,
        False,
    ]


def test_a_sale_that_failed_is_said_over_the_position_that_is_still_there(
    qtbot: QtBot,
) -> None:
    """The rows are what they were, so only the line above them changes."""
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.show()
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_count=1, entries=(_entry(1, "100", "1"),)),
            observed_at_utc=OBSERVED_AT,
        )
    )

    widget.show_close_error("Could not sell that Entry.")

    qtbot.waitUntil(widget.state_label.isVisible)
    assert widget.state_label.text() == "Could not sell that Entry."
    assert widget.table.rowCount() == 1


def test_the_order_button_fits_the_row_it_sits_in(qtbot: QtBot) -> None:
    """A row's button is part of the row, not a button on a form.

    Dressed as one it stood taller than the row that holds it and ran the
    width of a column stretched across the table.
    """
    widget = PositionBasketTable()
    qtbot.addWidget(widget)
    widget.setStyleSheet(DARK_THEME)
    widget.resize(1400, 300)
    widget.show()
    widget.show_snapshot(
        ready_position_basket_tab(
            basket_snapshot(entry_count=1, entries=(_entry(1, "100", "1"),)),
            observed_at_utc=OBSERVED_AT,
        )
    )
    qtbot.waitUntil(lambda: widget.close_button(0) is not None)
    button = _close_button(widget, 0)
    qtbot.waitUntil(lambda: button.width() > 0)

    table = widget.table
    assert button.height() <= table.rowHeight(0)
    # The column holds a button, so the room left over goes to the last column
    # of values instead of stretching this one across the table.
    close_column = len(PositionBasketTable.COLUMNS)
    assert table.columnWidth(close_column) < table.columnWidth(close_column - 1)
    assert button.width() < table.viewport().width() // 4
