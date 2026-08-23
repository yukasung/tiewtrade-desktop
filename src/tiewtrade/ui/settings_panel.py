from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

CLEAR_DATABASE_BUSY_TEXT = "Clearing…"
CLEAR_DATABASE_IDLE_TEXT = "Clear All Database"


class SettingsPanel(QWidget):
    """Settings as a page of the Bot Control card, not a window over it.

    It sits in the same `_bot_pages` stack as `SessionSetupWidget` and
    `BotControlWidget`, and the room this makes is for values yet to come —
    a Binance API key, eventually — not only the one action here today. The
    button always answers a click; whether it goes anywhere — and what to
    say if it does not — is for whoever owns the actual clearing to decide,
    the same way `PositionBasketTable.close_all_requested` leaves the
    confirming to its window rather than asking it here.
    """

    clear_database_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")

        heading = QLabel("SETTINGS")
        heading.setObjectName("eyebrow")

        self.clear_database_button = QPushButton(CLEAR_DATABASE_IDLE_TEXT)
        self.clear_database_button.setObjectName("destructiveButton")
        self.clear_database_button.clicked.connect(self.clear_database_requested)

        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("secondaryButton")
        self.back_button.clicked.connect(self.back_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(self.clear_database_button)
        layout.addStretch()
        layout.addWidget(self.back_button)

    def set_busy(self, busy: bool) -> None:
        self.clear_database_button.setEnabled(not busy)
        self.clear_database_button.setText(
            CLEAR_DATABASE_BUSY_TEXT if busy else CLEAR_DATABASE_IDLE_TEXT
        )
