"""Settings as a page of the Bot Control card, not a window over it.

It sits in the same `_bot_pages` stack as `SessionSetupWidget` and
`BotControlWidget`, and the room this makes is for values yet to come —
a Binance API key, eventually — not only the one action here today. The
button always answers a click; whether it goes anywhere — and what to
say if it does not — is for whoever owns the actual clearing to decide,
the same way `PositionBasketTable.close_all_requested` leaves the
confirming to its window rather than asking it here.

The LINE fields are filled in here and never read back out: the panel is
told whether a token is stored, and says so on the token field itself
rather than in a note beside it, while the token stays wherever the
machine keeps it.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.ui.toggle_switch import ToggleSwitch

CLEAR_DATABASE_BUSY_TEXT = "Clearing…"
CLEAR_DATABASE_IDLE_TEXT = "Clear All Database"
SAVE_LINE_BUSY_TEXT = "Saving…"
SAVE_LINE_IDLE_TEXT = "Save Settings"
TOKEN_STORED_PLACEHOLDER = "Saved — type a new token to replace it"
TOKEN_MISSING_PLACEHOLDER = "Messaging API channel access token"
TOKEN_UNREADABLE_TEXT = "The saved LINE settings could not be read on this machine."

type LineNotificationsReading = tuple[bool, str, bool]
"""Turned on, who the messages go to, and whether a token is stored for it."""


class SettingsPanel(QWidget):
    """The values and one-off actions that belong to the installation."""

    clear_database_requested = Signal()
    line_notifications_save_requested = Signal(bool, str, str)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")

        heading = QLabel("SETTINGS")
        heading.setObjectName("eyebrow")

        line_heading = QLabel("LINE NOTIFICATIONS")
        line_heading.setObjectName("eyebrow")

        # A switch rather than a tick: this setting is not a fact about the
        # Session, it is something left running or left off.
        self.line_enabled = ToggleSwitch()
        line_enabled_label = QLabel("Send a LINE message")
        line_enabled_row = QHBoxLayout()
        line_enabled_row.setSpacing(10)
        line_enabled_row.addWidget(self.line_enabled)
        line_enabled_row.addWidget(line_enabled_label)
        line_enabled_row.addStretch()
        self.line_target_id = QLineEdit()
        self.line_target_id.setPlaceholderText("User or group ID")
        self.line_access_token = QLineEdit()
        self.line_access_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.line_access_token.setPlaceholderText(TOKEN_MISSING_PLACEHOLDER)

        # Blank unless something is wrong: what a trader has to be told about
        # these settings is that they could not be read, not that they were.
        self.line_token_state = QLabel()
        self.line_token_state.setObjectName("fieldError")
        self.line_token_state.setWordWrap(True)
        self.line_token_state.setVisible(False)

        self.save_line_button = QPushButton(SAVE_LINE_IDLE_TEXT)
        self.save_line_button.setObjectName("secondaryButton")
        self.save_line_button.clicked.connect(self._save_line_notifications)

        line_form = QVBoxLayout()
        line_form.setContentsMargins(0, 0, 0, 0)
        line_form.setSpacing(12)
        line_form.addLayout(_field_row("Send to", self.line_target_id))
        line_form.addLayout(_field_row("Access token", self.line_access_token))

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
        layout.addWidget(line_heading)
        layout.addLayout(line_enabled_row)
        layout.addLayout(line_form)
        layout.addWidget(self.line_token_state)
        # The actions sit at the foot of the page, under the space the values
        # yet to come will fill, so they stay where a trader last left them
        # instead of sliding down the page as fields are added above.
        layout.addStretch()
        layout.addWidget(self.save_line_button)
        layout.addWidget(self.clear_database_button)
        layout.addWidget(self.back_button)

    def set_busy(self, busy: bool) -> None:
        self.clear_database_button.setEnabled(not busy)
        self.clear_database_button.setText(
            CLEAR_DATABASE_BUSY_TEXT if busy else CLEAR_DATABASE_IDLE_TEXT
        )
        self.save_line_button.setEnabled(not busy)
        self.save_line_button.setText(
            SAVE_LINE_BUSY_TEXT if busy else SAVE_LINE_IDLE_TEXT
        )

    def show_line_notifications(self, reading: LineNotificationsReading) -> None:
        """Fill the form from what is stored, leaving the token field empty.

        A token that is already saved is said to be there, never shown: the
        trader types one only to replace it, and a field filled with the old
        one would put a live token on screen for nothing.
        """
        enabled, target_id, token_stored = reading
        self.line_enabled.setChecked(enabled)
        self.line_target_id.setText(target_id)
        self.line_access_token.clear()
        self.line_access_token.setPlaceholderText(
            TOKEN_STORED_PLACEHOLDER if token_stored else TOKEN_MISSING_PLACEHOLDER
        )
        self.line_token_state.clear()
        self.line_token_state.setVisible(False)

    def say_line_notifications_unreadable(self) -> None:
        """Say the settings could not be read, rather than that there are none.

        An empty form and a form nobody could read look the same and mean
        opposite things: one is a Bot that will send nothing because it was
        never set up, the other a Bot that may well send and cannot say so.
        """
        self.line_token_state.setText(TOKEN_UNREADABLE_TEXT)
        self.line_token_state.setVisible(True)

    def _save_line_notifications(self) -> None:
        self.line_notifications_save_requested.emit(
            self.line_enabled.isChecked(),
            self.line_target_id.text().strip(),
            self.line_access_token.text().strip(),
        )


def _field_row(label: str, field: QWidget) -> QVBoxLayout:
    """A label with its field under it, filling the width it is given.

    The Setup screen states its fields this way, and these are the same kind
    of thing: a token is long enough that a field sharing its row with a
    label has nowhere to show what was typed into it.
    """
    row = QVBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(QLabel(label))
    row.addWidget(field)
    return row
