"""Every notice a Session raised, one row each, newest first.

The same shape the Position and Trade History tabs already use: a table for
the rows, an overlay for when there are none, and a label for the one
sentence a loading or unavailable read has to say. A row shows what the
message opened with and what every channel that was set up made of it — the
whole of what a trader would otherwise have had to ask the log for.
"""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
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

from tiewtrade.application.session_notices import SessionNoticeHistoryItem
from tiewtrade.ui.date_range_filter_bar import FilterBar
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.notification_presenter import notice_delivery_text, notice_headline
from tiewtrade.ui.table_column import CENTER, LEFT, Column, headers, table_item
from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay

COLUMNS: tuple[Column[SessionNoticeHistoryItem], ...] = (
    Column("Date", LEFT, lambda item: display_timestamp(item.raised_at_utc)),
    Column("Notice", LEFT, notice_headline),
    Column("Delivery", LEFT, notice_delivery_text),
)


class NotificationPage(QWidget):
    """Everything the Bot has said for the Session this screen is reading."""

    retry_requested = Signal()
    apply_filters_requested = Signal(object)
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("notificationPage")
        self.setAccessibleName("Notifications")

        self.filters = FilterBar()
        self.filters.apply_filters_requested.connect(self.apply_filters_requested)
        self.filters.reset_requested.connect(self.reset_requested)

        self.table = self._table()
        self.empty_state = TableEmptyStateOverlay(self.table)
        self.state_label = QLabel("")
        self.state_label.setObjectName("notificationState")
        self.state_label.setAccessibleName("Notification status")
        self.state_label.setProperty("stateMessage", True)
        self.state_label.setWordWrap(True)
        self.state_label.setVisible(False)
        self.retry_button = QPushButton("Try Again")
        self.retry_button.setObjectName("retryNotificationsButton")
        self.retry_button.setProperty("retryButton", True)
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self.retry_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filters)
        layout.addWidget(self.state_label)
        layout.addWidget(self.table, 1)
        state_row = QHBoxLayout()
        state_row.addStretch()
        state_row.addWidget(self.retry_button)
        layout.addLayout(state_row)

    @property
    def headers(self) -> tuple[str, ...]:
        return headers(COLUMNS)

    def show_items(self, items: tuple[SessionNoticeHistoryItem, ...]) -> None:
        """Show these rows, and take back whatever the read before this said."""
        self.filters.clear_error()
        self.empty_state.clear_message()
        self.state_label.setVisible(False)
        self.retry_button.setVisible(False)
        with QSignalBlocker(self.table):
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self.table.setRowCount(len(items))
            for row_index, item in enumerate(items):
                for column_index, column in enumerate(COLUMNS):
                    self.table.setItem(
                        row_index, column_index, table_item(column, item)
                    )

    def show_empty(self, message: str) -> None:
        self.filters.clear_error()
        self.state_label.setVisible(False)
        self.retry_button.setVisible(False)
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
        self.empty_state.show_message(message)

    def show_filter_error(self, message: str) -> None:
        self.filters.show_error(message)

    def show_loading(self, message: str) -> None:
        """Say a read is underway without taking rows that already loaded."""
        self.empty_state.clear_message()
        self.retry_button.setVisible(False)
        self.state_label.setText(message)
        self.state_label.setVisible(True)

    def show_unavailable(self, message: str) -> None:
        """Say the read failed, leaving whatever rows were already showing.

        The last-known rows stay up rather than being cleared: a read that
        could not be confirmed just now is not proof that what is on screen
        is wrong, and clearing it would read as the Session having said
        nothing when it has said plenty.
        """
        self.empty_state.clear_message()
        self.state_label.setText(message)
        self.state_label.setVisible(True)
        self.retry_button.setVisible(True)

    def _table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setObjectName("notificationGrid")
        table.setAccessibleName("Notifications")
        table.setHorizontalHeaderLabels(list(headers(COLUMNS)))
        for index in range(len(COLUMNS)):
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
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(
            len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch
        )
        return table
