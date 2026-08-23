from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketEntrySnapshot,
    BasketSnapshot,
    PositionBasketTabSnapshot,
    WorkspaceTabState,
)
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.entry_gate_display import blocked_entry_reason
from tiewtrade.ui.money_text import money_text, price_text
from tiewtrade.ui.sign_state import sign_colour
from tiewtrade.ui.table_column import (
    CENTER,
    LEFT,
    RIGHT,
    Column,
    headers,
    table_item,
)
from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay


@dataclass(frozen=True, slots=True)
class _Row:
    """One Entry, read against the Basket it belongs to.

    Some of a row's facts are the Entry's own and some are the Basket's — the
    lifecycle is one gate for every Entry — so a column is handed both.
    """

    basket: BasketSnapshot
    entry: BasketEntrySnapshot


def _filled_at(row: _Row) -> str:
    return display_timestamp(row.entry.filled_at_utc)


def _price(row: _Row) -> str:
    return price_text(row.entry.price)


def _take_profit_price(row: _Row) -> str:
    """The target this Entry alone is sold at."""
    return price_text(row.entry.take_profit_price)


def _entry_gate(row: _Row) -> str:
    """Whether this Entry's own Pair is what a Cooldown is blocking, and why."""
    gate = row.basket.entry_gate_for(row.entry.entry_number)
    return blocked_entry_reason(gate) or "Active"


def _quantity(row: _Row) -> str:
    return format(row.entry.quantity, "f")


def _notional(row: _Row) -> str:
    return money_text(row.entry.notional)


def _pnl(row: _Row) -> str:
    """What the Entry is worth against the price, once there is one.

    A Basket read out of the database is shown before its first candle, and
    an unmarked Entry has no result yet. A dash says the question was asked
    and has no answer, where 0.00 would say it broke even.
    """
    pnl = row.entry.unrealized_pnl
    return BLANK if pnl is None else money_text(pnl)


def _pnl_colour(row: _Row) -> str | None:
    pnl = row.entry.unrealized_pnl
    return None if pnl is None else sign_colour(pnl)


class PositionBasketTable(QWidget):
    """Every Entry of the open Basket, one row each.

    A trader enters the same Basket more than once, so an averaged single row
    hides the prices actually paid. The rows are the Entries, each with the
    price it paid and the target it is sold at.

    Every column reads at the same level — the Entry's own. The Basket's
    average used to be restated on every row, which read as a third price
    beside the two the row paid and aimed at, and invited the target to be
    read as measured from it. It is measured from the Entry's own price, so
    the average is no longer printed here. Only the lifecycle, one gate for
    every Entry, is still the Basket's.
    """

    # Measured numbers hang on the right so their digits line up and a minus
    # sign cannot hide; short labels sit centred; text that changes length
    # stays left so its edge does not move under the reader.
    COLUMNS: tuple[Column[_Row], ...] = (
        Column("Date", LEFT, _filled_at),
        Column("Entry Price", RIGHT, _price),
        # Beside the price paid, so the distance left to run reads at a glance.
        Column("Take Profit", RIGHT, _take_profit_price),
        Column("Quantity", RIGHT, _quantity),
        Column("Total", RIGHT, _notional),
        Column("Unrealized P/L", RIGHT, _pnl, _pnl_colour),
        Column("Status", LEFT, _entry_gate),
    )
    # Binance names the column after the position and the button after the
    # order that closes it — [Market] under [Close Position] — because the
    # button is the order type, not the verb. One kind of order can be sent
    # from here, so there is one button.
    CLOSE_HEADER = "Close Position"
    CLOSE_LABEL = "Market"
    HEADERS = (*headers(COLUMNS), CLOSE_HEADER)
    ALIGNMENTS = tuple(column.alignment for column in COLUMNS)

    #: The Entry a row asked to be sold at the price the market is at.
    close_entry_requested = Signal(int)
    #: The Basket asked to lift the Cooldown blocking its next Entry.
    cancel_cooldown_requested = Signal()
    #: The whole open Position asked to be sold at the price the market is at.
    close_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("positionBasketTable")
        self.setAccessibleName("Position")

        self.table = self._table()
        self._closing_enabled = True
        self._cancelling_enabled = True
        #: The Basket the Cancel Cooldown button asks about, if it is shown.
        self.basket: BasketSnapshot | None = None
        self.empty_state = TableEmptyStateOverlay(self.table)
        self.state_label = QLabel("No open Position")
        self.state_label.setObjectName("positionBasketState")
        self.state_label.setAccessibleName("Position status")
        self.state_label.setProperty("stateMessage", True)
        self.state_label.setWordWrap(True)
        # Owned here, where the Cooldown it acts on is read — shown wherever
        # the workspace around this table chooses to put it, on the same row
        # as the tabs it sits beside. A caller that never places it still
        # gets a button that shows and hides correctly, sized to its own text.
        self.cancel_cooldown_button = QPushButton("Cancel Cooldown", self)
        self.cancel_cooldown_button.setObjectName("cancelCooldownButton")
        self.cancel_cooldown_button.setAccessibleName("Cancel Cooldown")
        self.cancel_cooldown_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_cooldown_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.cancel_cooldown_button.setVisible(False)
        self.cancel_cooldown_button.clicked.connect(
            lambda _checked=False: self.cancel_cooldown_requested.emit()
        )
        # Owned here too, alongside the button it sits next to — shown
        # whenever the open Basket has something left to sell, not only
        # during a Cooldown, since it sells the whole Position at once.
        self.close_all_button = QPushButton("Close All", self)
        self.close_all_button.setObjectName("closeAllButton")
        self.close_all_button.setAccessibleName("Close All at Market")
        self.close_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_all_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.close_all_button.setVisible(False)
        self.close_all_button.clicked.connect(
            lambda _checked=False: self.close_all_requested.emit()
        )
        self._closing_all_enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.state_label)
        layout.addWidget(self.table, 1)

    @property
    def headers(self) -> tuple[str, ...]:
        return self.HEADERS

    def show_snapshot(self, value: object) -> None:
        if not isinstance(value, PositionBasketTabSnapshot):
            return
        self._show_basket(value.basket)
        self._show_state(value)
        self._show_cooldown(value.basket)
        self._show_close_all(value.basket)

    def _show_basket(self, basket: BasketSnapshot | None) -> None:
        rows = (
            ()
            if basket is None
            else tuple(_Row(basket, entry) for entry in basket.entries)
        )
        with QSignalBlocker(self.table):
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                for position, column in enumerate(self.COLUMNS):
                    self.table.setItem(index, position, table_item(column, row))
                self.table.setCellWidget(
                    index, len(self.COLUMNS), self._close_cell(row)
                )

    def _show_state(self, snapshot: PositionBasketTabSnapshot) -> None:
        text = self._state_text(snapshot)
        self.state_label.setText(text)
        if snapshot.state is WorkspaceTabState.EMPTY:
            self.empty_state.show_message(text)
            self.state_label.setVisible(False)
            return
        self.empty_state.clear_message()
        self.state_label.setVisible(bool(text))

    def _show_cooldown(self, basket: BasketSnapshot | None) -> None:
        self.basket = basket
        in_cooldown = (
            basket is not None and basket.entry_gate is BasketEntryGate.COOLDOWN
        )
        self.cancel_cooldown_button.setVisible(in_cooldown)
        self.cancel_cooldown_button.setEnabled(in_cooldown and self._cancelling_enabled)

    def _show_close_all(self, basket: BasketSnapshot | None) -> None:
        """Offered whenever there is a Position, not only during a Cooldown."""
        has_open_position = basket is not None and bool(basket.entries)
        self.close_all_button.setVisible(has_open_position)
        self.close_all_button.setEnabled(
            has_open_position and self._closing_all_enabled
        )

    @staticmethod
    def _state_text(snapshot: PositionBasketTabSnapshot) -> str:
        if snapshot.state is WorkspaceTabState.LOADING:
            return "Loading Position…"
        if snapshot.state is WorkspaceTabState.EMPTY:
            return "No open Position"
        if snapshot.state is WorkspaceTabState.ERROR:
            return snapshot.message or ""
        if snapshot.state is WorkspaceTabState.STALE:
            data_as_of_utc = snapshot.data_as_of_utc
            if data_as_of_utc is None:
                return "Stale Position data"
            stamp = display_timestamp(data_as_of_utc)
            return f"Stale Position data as of {stamp}"
        return ""

    def _close_cell(self, row: _Row) -> QWidget:
        """The button, held in the middle of the room a cell leaves it.

        A cell widget is stretched to the whole cell, so on its own the button
        ran the width of the column and stood taller than the row. What the
        cell leaves is the height of a line of text, which is the size the
        button is drawn at.
        """
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._close_button(row), 0, Qt.AlignmentFlag.AlignCenter)
        return cell

    def _close_button(self, row: _Row) -> QPushButton:
        """The order this row can send, which is the only one it can send.

        The Entry number travels with the button rather than the row it sits
        in: rows are rebuilt on every price, and a number cannot drift the way
        a position in a table can.
        """
        entry_number = row.entry.entry_number
        button = QPushButton(self.CLOSE_LABEL)
        button.setObjectName("positionEntryCloseButton")
        button.setAccessibleName(f"Close Entry {entry_number} at Market")
        cooling_down = (
            row.basket.entry_gate_for(entry_number) is BasketEntryGate.COOLDOWN
        )
        button.setEnabled(self._closing_enabled and not cooling_down)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(
            lambda _checked=False: self.close_entry_requested.emit(entry_number)
        )
        return button

    def show_close_error(self, message: str) -> None:
        """Say a sale did not happen, over the Position that is still there.

        The rows are what they were, so the tab is not repainted: only the line
        above them changes, and the next reading of the Basket clears it.
        """
        self.state_label.setText(message)
        self.state_label.setVisible(True)

    def set_closing_enabled(self, enabled: bool) -> None:
        """Take the buttons away while a sale of theirs is still in flight."""
        self._closing_enabled = enabled
        for index in range(self.table.rowCount()):
            button = self.close_button(index)
            if button is not None:
                button.setEnabled(enabled)

    def show_cancel_error(self, message: str) -> None:
        """Say a waiver did not happen, over the Position that is still there."""
        self.state_label.setText(message)
        self.state_label.setVisible(True)

    def set_cancelling_enabled(self, enabled: bool) -> None:
        """Take the button away while a waiver of its own is still in flight."""
        self._cancelling_enabled = enabled
        self.cancel_cooldown_button.setEnabled(
            enabled and self.cancel_cooldown_button.isVisible()
        )

    def set_closing_all_enabled(self, enabled: bool) -> None:
        """Take the button away while a sale of the whole Position is in flight."""
        self._closing_all_enabled = enabled
        self.close_all_button.setEnabled(enabled and self.close_all_button.isVisible())

    def set_selling_enabled(self, enabled: bool) -> None:
        """Every way out of the Position at once, for a sale that takes them all.

        Close All sells what the per-row buttons sell, so leaving those live
        while it is in flight would offer a second sale of an Entry already
        being sold.
        """
        self.set_closing_enabled(enabled)
        self.set_closing_all_enabled(enabled)

    def close_button(self, row: int) -> QPushButton | None:
        """The order button of a row, wherever the cell keeps it."""
        cell = self.table.cellWidget(row, len(self.COLUMNS))
        if cell is None:
            return None
        return cell.findChild(QPushButton)

    def _table(self) -> QTableWidget:
        table = QTableWidget(0, len(self.HEADERS))
        table.setObjectName("positionBasketGrid")
        table.setAccessibleName("Position")
        table.setHorizontalHeaderLabels(list(self.HEADERS))
        # Headings sit centred over every column, whichever way the values
        # under them run. The cells keep their own alignment, so figures
        # still line up on one right edge to be read down.
        for index in range(len(self.HEADERS)):
            header_item = table.horizontalHeaderItem(index)
            if header_item is not None:
                header_item.setTextAlignment(CENTER)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        table.setWordWrap(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        # The slack goes to the last column of values, not to the one holding
        # a button: stretched, that column ran the width of the table with a
        # button stretched along with it.
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(
            len(self.COLUMNS) - 1, QHeaderView.ResizeMode.Stretch
        )
        return table
