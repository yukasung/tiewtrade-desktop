from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

CLEAR_DATABASE_HINT = "Stop the bot before clearing the database."
CLEAR_DATABASE_BUSY_TEXT = "Clearing…"
CLEAR_DATABASE_IDLE_TEXT = "Clear All Database"


class SettingsDialog(QDialog):
    """The seed of a Settings panel: one destructive action, asked elsewhere.

    The button is clickable whenever the Bot is not actually trading or
    moving between states. Stopped, Blocked, and a freshly Configured Session
    all show the same editable Setup form, whose only way onward retires
    whatever Session was there and creates a fresh one — so a cleared
    database never leaves that form pointed at a row that just went. Whether
    the click goes anywhere is for whoever owns the actual clearing to
    decide, the same way `PositionBasketTable.close_all_requested` leaves the
    confirming to its window rather than asking it here.
    """

    clear_database_requested = Signal()

    def __init__(
        self, parent: QWidget | None = None, *, can_clear_database: bool
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self.clear_database_button = QPushButton(CLEAR_DATABASE_IDLE_TEXT)
        self.clear_database_button.setObjectName("destructiveButton")
        self.clear_database_button.setEnabled(can_clear_database)
        self.clear_database_button.clicked.connect(self.clear_database_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self.clear_database_button)
        if not can_clear_database:
            hint = QLabel(CLEAR_DATABASE_HINT)
            hint.setWordWrap(True)
            hint.setObjectName("supportingText")
            layout.addWidget(hint)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        assert close_button is not None
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    def set_busy(self, busy: bool) -> None:
        self.clear_database_button.setEnabled(not busy)
        self.clear_database_button.setText(
            CLEAR_DATABASE_BUSY_TEXT if busy else CLEAR_DATABASE_IDLE_TEXT
        )
