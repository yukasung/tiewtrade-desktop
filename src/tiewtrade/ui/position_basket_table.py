from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.trading_workspace import (
    BasketEntrySnapshot,
    BasketSnapshot,
    PositionBasketTabSnapshot,
    WorkspaceTabState,
)
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.entry_gate_display import blocked_entry_reason
from tiewtrade.ui.money_text import money_text, price_text
from tiewtrade.ui.sign_state import paint_by_sign, sign_colour
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
    """Whether the Basket can still take an Entry, and when it cannot, why."""
    return blocked_entry_reason(row.basket) or "Active"


def _quantity(row: _Row) -> str:
    return format(row.entry.quantity, "f")


def _notional(row: _Row) -> str:
    return money_text(row.entry.notional)


def _pnl(row: _Row) -> str:
    return money_text(row.entry.unrealized_pnl)


def _pnl_colour(row: _Row) -> str | None:
    return sign_colour(row.entry.unrealized_pnl)


class PositionBasketTable(QWidget):
    """Every Entry of the open Basket, one row each.

    A trader enters the same Basket more than once, so an averaged single row
    hides the prices actually paid. The rows are the Entries, each with the
    price it paid and the target it is sold at. Facts the Basket holds for all
    of them — its lifecycle — are restated on every row so a row can be read
    on its own; the market it trades and the totals it sums to, which no row
    can carry, frame the table instead.
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
    HEADERS = headers(COLUMNS)
    ALIGNMENTS = tuple(column.alignment for column in COLUMNS)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("positionBasketTable")
        self.setAccessibleName("Position")

        self.table = self._table()
        self.empty_state = TableEmptyStateOverlay(self.table)
        self.state_label = QLabel("No open Position")
        self.state_label.setObjectName("positionBasketState")
        self.state_label.setAccessibleName("Position status")
        self.state_label.setProperty("stateMessage", True)
        self.state_label.setWordWrap(True)
        self.total_label = QLabel()
        self.total_label.setObjectName("positionBasketTotal")
        self.total_label.setAccessibleName("Position total")
        self.total_label.setWordWrap(True)
        self.total_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.state_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.total_label)

    @property
    def headers(self) -> tuple[str, ...]:
        return self.HEADERS

    def show_snapshot(self, value: object) -> None:
        if not isinstance(value, PositionBasketTabSnapshot):
            return
        self._show_basket(value.basket)
        self._show_state(value)

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
        self._show_basket_total(basket)

    def _show_basket_total(self, basket: BasketSnapshot | None) -> None:
        """What the Basket sums to, which no single Entry row can carry.

        The symbol and market it trades are not restated here: the chart above
        and the session panel beside it already name them, and a third copy
        only takes a line away from the Entries.
        """
        if basket is None:
            self.total_label.setVisible(False)
            return
        self.total_label.setText(
            f"{basket.entry_count} "
            f"{'Entry' if basket.entry_count == 1 else 'Entries'} · "
            f"Total {format(basket.total_quantity, 'f')} · "
            f"Average {price_text(basket.average_entry_price)} · "
            f"Unrealized {money_text(basket.unrealized_pnl)}"
        )
        paint_by_sign(self.total_label, basket.unrealized_pnl)
        self.total_label.setVisible(True)

    def _show_state(self, snapshot: PositionBasketTabSnapshot) -> None:
        text = self._state_text(snapshot)
        self.state_label.setText(text)
        if snapshot.state is WorkspaceTabState.EMPTY:
            self.empty_state.show_message(text)
            self.state_label.setVisible(False)
            return
        self.empty_state.clear_message()
        self.state_label.setVisible(bool(text))

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

    def _table(self) -> QTableWidget:
        table = QTableWidget(0, len(self.COLUMNS))
        table.setObjectName("positionBasketGrid")
        table.setAccessibleName("Position")
        table.setHorizontalHeaderLabels(list(self.HEADERS))
        # Headings sit centred over every column, whichever way the values
        # under them run. The cells keep their own alignment, so figures
        # still line up on one right edge to be read down.
        for index in range(len(self.COLUMNS)):
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
        table.horizontalHeader().setStretchLastSection(True)
        return table
