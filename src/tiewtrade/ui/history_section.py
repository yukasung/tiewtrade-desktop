"""One half of the history screen: a filter, a table, and what it has to say.

Position History and Trade History are built the same way — the same table,
the same overlay for an empty one, the same line of state under it with the
same retry beside it — and were written twice. Built here once, the two halves
differ only where they mean to: what a row is, and what "nothing found" says.

What each half *does* with a result stays where it is. The two are not the
same state machine — one remembers that a result arrived, the other reads the
rows it has — and folding that apart into flags would hide the difference
rather than remove it.
"""

from typing import Any

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.ui.table_column import CENTER, Column, headers
from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay


class HistorySection(QWidget):
    """A filter bar, the table it reads into, and the state line beneath."""

    retry_requested = Signal()

    def __init__(
        self,
        *,
        filters: QWidget,
        columns: tuple[Column[Any], ...],
        table_object_name: str,
        state_object_name: str,
        retry_object_name: str,
        accessible_name: str,
        empty_message: str,
        selectable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._empty_message = empty_message
        self.filters = filters
        self.table = _table(table_object_name, accessible_name, columns)
        if not selectable:
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.empty_state = TableEmptyStateOverlay(self.table)
        self.state = _state_label(state_object_name, f"{accessible_name} status")
        self.retry_button = _retry_button(retry_object_name)
        self.retry_button.clicked.connect(self.retry_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.filters)
        layout.addWidget(self.table, 1)
        state_row = QHBoxLayout()
        state_row.addWidget(self.state)
        state_row.addStretch()
        state_row.addWidget(self.retry_button)
        layout.addLayout(state_row)

    def show_state(self, message: str) -> None:
        """Nothing found reads inside the empty table; anything else below it."""
        self.state.setText(message)
        if message == self._empty_message:
            self.empty_state.show_message(message)
            self.state.setVisible(False)
            return
        self.empty_state.clear_message()
        self.state.setVisible(True)

    def clear_state(self) -> None:
        self.empty_state.clear_message()
        self.state.clear()
        self.state.setVisible(False)
        self.retry_button.setVisible(False)

    def clear_rows(self) -> None:
        blocker = QSignalBlocker(self.table)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(0)
        del blocker

    def focus_chain(self) -> tuple[QWidget, ...]:
        """The filter read first, then the table it reads into."""
        chain = getattr(self.filters, "focus_chain", None)
        return (*(chain() if callable(chain) else ()), self.table)


def _table(
    object_name: str,
    accessible_name: str,
    columns: tuple[Column[Any], ...],
) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setObjectName(object_name)
    table.setAccessibleName(accessible_name)
    table.setHorizontalHeaderLabels(list(headers(columns)))
    # Headings sit centred over every column, whichever way the values under
    # them run. The cells keep their own alignment, so figures still line up
    # on one right edge to be read down.
    for index in range(len(columns)):
        header_item = table.horizontalHeaderItem(index)
        if header_item is not None:
            header_item.setTextAlignment(CENTER)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(False)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    # Deliberately not ResizeToContents: that mode re-measures every row of a
    # column on every cell written, which froze the window for seconds on each
    # refresh. `fill_table` measures once instead.
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    # The last column here holds figures, right aligned. Stretching it would
    # hand it all the room left over and push those figures to the far edge,
    # away from the Total they are read against. The spare width is better
    # left empty beside the table.
    table.horizontalHeader().setStretchLastSection(False)
    return table


def _state_label(object_name: str, accessible_name: str) -> QLabel:
    label = QLabel()
    label.setObjectName(object_name)
    label.setAccessibleName(accessible_name)
    label.setProperty("stateMessage", True)
    label.setWordWrap(True)
    label.setVisible(False)
    return label


def _retry_button(object_name: str) -> QPushButton:
    button = QPushButton("Try Again")
    button.setObjectName(object_name)
    button.setProperty("retryButton", True)
    button.setVisible(False)
    return button
