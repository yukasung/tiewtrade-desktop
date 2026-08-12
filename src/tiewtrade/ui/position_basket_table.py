from decimal import Decimal

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.trading_workspace import (
    BasketSnapshot,
    PositionBasketTabSnapshot,
    WorkspaceTabState,
)
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.entry_gate_display import blocked_entry_reason
from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR

_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_CENTER = Qt.AlignmentFlag.AlignCenter
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class PositionBasketTable(QWidget):
    # Measured numbers hang on the right so their digits line up and a minus
    # sign cannot hide; short labels sit centred; text that changes length
    # stays left so its edge does not move under the reader.
    COLUMNS = (
        ("Symbol", _LEFT),
        ("Market Type", _CENTER),
        ("Entry Count", _CENTER),
        ("Total Quantity", _RIGHT),
        ("Average Entry Price", _RIGHT),
        ("Basket Take Profit", _RIGHT),
        ("Unrealized PnL", _RIGHT),
        ("Basket Lifecycle", _LEFT),
    )
    HEADERS = tuple(header for header, _alignment in COLUMNS)
    ALIGNMENTS = tuple(alignment for _header, alignment in COLUMNS)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("positionBasketTable")
        self.setAccessibleName("Position / Basket")

        self.table = self._table()
        self.empty_state = TableEmptyStateOverlay(self.table)
        self.state_label = QLabel("No open Position or Basket")
        self.state_label.setObjectName("positionBasketState")
        self.state_label.setAccessibleName("Position / Basket status")
        self.state_label.setProperty("stateMessage", True)
        self.state_label.setWordWrap(True)

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

    def _show_basket(self, basket: BasketSnapshot | None) -> None:
        blocker = QSignalBlocker(self.table)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(0 if basket is None else 1)
        if basket is not None:
            for column_index, text in enumerate(self._basket_row(basket)):
                self.table.setItem(
                    0,
                    column_index,
                    self._table_item(text, self.ALIGNMENTS[column_index]),
                )
            self._colour_pnl(basket.unrealized_pnl)
        del blocker

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
    def _basket_row(basket: BasketSnapshot) -> tuple[str, ...]:
        return (
            basket.symbol,
            _enum_text(basket.market_type),
            str(basket.entry_count),
            format(basket.total_quantity, "f"),
            format(basket.average_entry_price, "f"),
            format(basket.take_profit_price, "f"),
            _pnl_text(basket.unrealized_pnl),
            _entry_gate_text(basket),
        )

    def _colour_pnl(self, value: Decimal) -> None:
        """Let the figure's own colour say gain or loss, so no word has to."""
        colour = _pnl_colour(value)
        item = self.table.item(0, self.HEADERS.index("Unrealized PnL"))
        if colour is None or item is None:
            return
        item.setForeground(QBrush(QColor(colour)))

    @staticmethod
    def _state_text(snapshot: PositionBasketTabSnapshot) -> str:
        if snapshot.state is WorkspaceTabState.LOADING:
            return "Loading Position / Basket…"
        if snapshot.state is WorkspaceTabState.EMPTY:
            return "No open Position or Basket"
        if snapshot.state is WorkspaceTabState.ERROR:
            return snapshot.message or ""
        if snapshot.state is WorkspaceTabState.STALE:
            data_as_of_utc = snapshot.data_as_of_utc
            if data_as_of_utc is None:
                return "Stale Position / Basket data"
            stamp = display_timestamp(data_as_of_utc)
            return f"Stale Position / Basket data as of {stamp}"
        return ""

    def _table(self) -> QTableWidget:
        table = QTableWidget(0, len(self.HEADERS))
        table.setObjectName("positionBasketGrid")
        table.setAccessibleName("Position / Basket")
        table.setHorizontalHeaderLabels(list(self.HEADERS))
        # A header that does not follow its column reads as a different column.
        for column_index, alignment in enumerate(self.ALIGNMENTS):
            header_item = table.horizontalHeaderItem(column_index)
            if header_item is not None:
                header_item.setTextAlignment(alignment)
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

    @staticmethod
    def _table_item(text: str, alignment: Qt.AlignmentFlag) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(alignment)
        return item


def _enum_text(value: str) -> str:
    return value.replace("_", " ").title()


def _entry_gate_text(basket: BasketSnapshot) -> str:
    return blocked_entry_reason(basket) or "Active"


def _pnl_text(value: Decimal) -> str:
    return f"{format(value, '.2f')} USDT"


def _pnl_colour(value: Decimal) -> str | None:
    """No colour at break-even: neither reading is true, so the row stays plain."""
    if value > 0:
        return POSITIVE_COLOR
    if value < 0:
        return NEGATIVE_COLOR
    return None
