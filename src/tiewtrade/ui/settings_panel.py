"""Settings as a page of the Bot Control card, not a window over it.

It sits in the same `_bot_pages` stack as `SessionSetupWidget` and
`BotControlWidget`, and the room this makes is for values yet to come —
a Binance API key, eventually — not only the one action here today. The
button always answers a click; whether it goes anywhere — and what to
say if it does not — is for whoever owns the actual clearing to decide,
the same way `PositionBasketTable.close_all_requested` leaves the
confirming to its window rather than asking it here.

The LINE and Telegram fields are filled in from what is stored, the tokens
among them: a form that offers to change a token has to show which one it is
changing. They arrive covered and stay that way unless the trader asks to see
one, and each token is uncovered on its own — revealing the one being typed
should not put the other on screen beside it.

The two channels are a section each rather than one section with a choice in
it, because they are not a choice: a trader may run either, both, or neither,
and each is turned on by its own switch.

The Title at the top is the one value here that is read back out on screen
rather than sent anywhere: it is what the toolbar says, all of it, so someone
looking after several installations can tell which one the window in front of
them is. What it says is the trader's business — a customer's name, a machine,
a strategy they are trying — so the form asks for a title, offers the name of
the application as the one to start from, and lets them take that out.
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.ui.bot_title import bot_title_or_default
from tiewtrade.ui.toggle_switch import ToggleSwitch

CLEAR_DATABASE_BUSY_TEXT = "Clearing…"
CLEAR_DATABASE_IDLE_TEXT = "Clear All Database"
SAVE_SETTINGS_BUSY_TEXT = "Saving…"
SAVE_SETTINGS_IDLE_TEXT = "Save Settings"
BOT_TITLE_PLACEHOLDER = "Shown at the top of the window"
BOT_TITLE_MAX_LENGTH = 60
"""As much of a remark as the toolbar can hold beside the name of the app.

Whoever stores the title trims it too, against a file edited by hand rather
than through this form — this is the same rule said where the typing happens,
so a trader is stopped at the last character instead of losing the rest of
what they typed on a save that looked like it worked.
"""
TOKEN_MISSING_PLACEHOLDER = "Messaging API channel access token"
TOKEN_UNREADABLE_TEXT = "The saved LINE settings could not be read on this machine."
TELEGRAM_TOKEN_MISSING_PLACEHOLDER = "Bot token from BotFather"
TELEGRAM_TOKEN_UNREADABLE_TEXT = (
    "The saved Telegram settings could not be read on this machine."
)
REVEAL_TOKEN_TEXT = "Show the token being typed"
HIDE_TOKEN_TEXT = "Hide the token being typed"

_EYE_ICON_SIZE = 18
_EYE_ICON_COLOR = "#94A3B8"

type LineNotificationsReading = tuple[bool, str, str]
"""Turned on, who the messages go to, and the token they are sent with."""

type TelegramNotificationsReading = tuple[bool, str, str]
"""The same three things, for the chat a Telegram bot sends into."""


class SettingsPanel(QWidget):
    """The values and one-off actions that belong to the installation."""

    clear_database_requested = Signal()
    #: The whole page at once: the Title, then LINE, then Telegram.
    settings_save_requested = Signal(str, bool, str, str, bool, str, str)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")

        heading = QLabel("SETTINGS")
        heading.setObjectName("eyebrow")

        # A remark, not an identity: nothing is bound to it and nothing reads
        # it but the toolbar, so it may be changed or emptied at any time,
        # while the Bot is running included. It gets no heading of its own —
        # a section called TITLE over a field called Title said it twice, and
        # the eyebrow above already says what page this is.
        self.bot_title = QLineEdit()
        self.bot_title.setMaxLength(BOT_TITLE_MAX_LENGTH)
        self.bot_title.setPlaceholderText(BOT_TITLE_PLACEHOLDER)
        bot_form = QVBoxLayout()
        bot_form.setContentsMargins(0, 0, 0, 0)
        bot_form.setSpacing(12)
        bot_form.addLayout(_field_row("Title", self.bot_title))

        line_heading = QLabel("LINE NOTIFICATIONS")
        line_heading.setObjectName("eyebrow")

        # A switch rather than a tick: this setting is not a fact about the
        # Session, it is something left running or left off.
        self.line_enabled = ToggleSwitch()
        line_enabled_row = _switch_row(self.line_enabled, "Send a LINE message")
        self.line_target_id = QLineEdit()
        self.line_target_id.setPlaceholderText("User or group ID")
        self.line_access_token, self.reveal_token = _covered_field(
            self, TOKEN_MISSING_PLACEHOLDER
        )
        # Blank unless something is wrong: what a trader has to be told about
        # these settings is that they could not be read, not that they were.
        self.line_token_state = _error_line()

        telegram_heading = QLabel("TELEGRAM NOTIFICATIONS")
        telegram_heading.setObjectName("eyebrow")

        self.telegram_enabled = ToggleSwitch()
        telegram_enabled_row = _switch_row(
            self.telegram_enabled, "Send a Telegram message"
        )
        # A chat id, not a username: a bot can only send into a chat it has
        # been added to, and that chat is named by a number the trader reads
        # off their own bot rather than by the handle they see in the app.
        self.telegram_chat_id = QLineEdit()
        self.telegram_chat_id.setPlaceholderText("Chat, group or channel ID")
        self.telegram_bot_token, self.reveal_telegram_token = _covered_field(
            self, TELEGRAM_TOKEN_MISSING_PLACEHOLDER
        )
        self.telegram_token_state = _error_line()

        self.save_settings_button = QPushButton(SAVE_SETTINGS_IDLE_TEXT)
        self.save_settings_button.setObjectName("secondaryButton")
        self.save_settings_button.clicked.connect(self._save_settings)

        line_form = QVBoxLayout()
        line_form.setContentsMargins(0, 0, 0, 0)
        line_form.setSpacing(12)
        line_form.addLayout(_field_row("Send to", self.line_target_id))
        line_form.addLayout(_field_row("Access token", self.line_access_token))

        telegram_form = QVBoxLayout()
        telegram_form.setContentsMargins(0, 0, 0, 0)
        telegram_form.setSpacing(12)
        telegram_form.addLayout(_field_row("Send to", self.telegram_chat_id))
        telegram_form.addLayout(_field_row("Bot token", self.telegram_bot_token))

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
        layout.addLayout(bot_form)
        layout.addWidget(line_heading)
        layout.addLayout(line_enabled_row)
        layout.addLayout(line_form)
        layout.addWidget(self.line_token_state)
        layout.addWidget(telegram_heading)
        layout.addLayout(telegram_enabled_row)
        layout.addLayout(telegram_form)
        layout.addWidget(self.telegram_token_state)
        # The actions sit at the foot of the page, under the space the values
        # yet to come will fill, so they stay where a trader last left them
        # instead of sliding down the page as fields are added above.
        layout.addStretch()
        layout.addWidget(self.save_settings_button)
        layout.addWidget(self.clear_database_button)
        layout.addWidget(self.back_button)

    def set_busy(self, busy: bool) -> None:
        self.clear_database_button.setEnabled(not busy)
        self.clear_database_button.setText(
            CLEAR_DATABASE_BUSY_TEXT if busy else CLEAR_DATABASE_IDLE_TEXT
        )
        self.save_settings_button.setEnabled(not busy)
        self.save_settings_button.setText(
            SAVE_SETTINGS_BUSY_TEXT if busy else SAVE_SETTINGS_IDLE_TEXT
        )

    def show_bot_title(self, title: str) -> None:
        """Fill the Title from what is stored, or from the name to start from.

        An installation nobody has titled shows the application's own name as
        text in the field rather than as a greyed-out suggestion behind it:
        the point of the default is that a trader can edit it or take it out,
        and a placeholder is neither.
        """
        self.bot_title.setText(bot_title_or_default(title))

    def show_line_notifications(self, reading: LineNotificationsReading) -> None:
        """Fill the form from what is stored, the token field included.

        The token comes back covered, and the page is left the way it opens
        rather than the way it was last typed into: whatever was revealed
        before is hidden again with it.
        """
        enabled, target_id, access_token = reading
        self.line_enabled.setChecked(enabled)
        self.line_target_id.setText(target_id)
        self.reveal_token.setChecked(False)
        self.line_access_token.setText(access_token)
        self.line_token_state.clear()
        self.line_token_state.setVisible(False)

    def show_telegram_notifications(
        self, reading: TelegramNotificationsReading
    ) -> None:
        """Fill the Telegram half of the form, on the same terms as the LINE half."""
        enabled, chat_id, bot_token = reading
        self.telegram_enabled.setChecked(enabled)
        self.telegram_chat_id.setText(chat_id)
        self.reveal_telegram_token.setChecked(False)
        self.telegram_bot_token.setText(bot_token)
        self.telegram_token_state.clear()
        self.telegram_token_state.setVisible(False)

    def say_line_notifications_unreadable(self) -> None:
        """Say the settings could not be read, rather than that there are none.

        An empty form and a form nobody could read look the same and mean
        opposite things: one is a Bot that will send nothing because it was
        never set up, the other a Bot that may well send and cannot say so.
        """
        self.line_token_state.setText(TOKEN_UNREADABLE_TEXT)
        self.line_token_state.setVisible(True)

    def say_telegram_notifications_unreadable(self) -> None:
        """The same thing said under the Telegram fields, for the same reason.

        Said under the channel it is about rather than once for the page: the
        two are stored apart, and a trader whose LINE settings read back fine
        should not be told to go looking at them.
        """
        self.telegram_token_state.setText(TELEGRAM_TOKEN_UNREADABLE_TEXT)
        self.telegram_token_state.setVisible(True)

    def _save_settings(self) -> None:
        self.settings_save_requested.emit(
            self.bot_title.text().strip(),
            self.line_enabled.isChecked(),
            self.line_target_id.text().strip(),
            self.line_access_token.text().strip(),
            self.telegram_enabled.isChecked(),
            self.telegram_chat_id.text().strip(),
            self.telegram_bot_token.text().strip(),
        )


def _eye_icon(*, crossed: bool) -> QIcon:
    """An eye, struck through while what it would show is still covered.

    The stroke reads as the offer every other password field makes — click
    to see this — rather than as a report of what the field is doing, so it
    is on the button that uncovers the token and off the one that hides it.
    """
    # Painted at 3x and tagged, so the outline stays smooth on a retina screen.
    ratio = 3
    pixmap = QPixmap(_EYE_ICON_SIZE * ratio, _EYE_ICON_SIZE * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(_EYE_ICON_COLOR), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    outline = QPainterPath(QPointF(1.8, 9.0))
    outline.quadTo(QPointF(9.0, 1.6), QPointF(16.2, 9.0))
    outline.quadTo(QPointF(9.0, 16.4), QPointF(1.8, 9.0))
    painter.drawPath(outline)
    painter.drawEllipse(QRectF(6.4, 6.4, 5.2, 5.2))
    if crossed:
        painter.drawLine(QPointF(3.4, 15.0), QPointF(14.6, 3.0))
    painter.end()
    icon = QIcon()
    icon.addPixmap(pixmap)
    return icon


def _covered_field(owner: QWidget, placeholder: str) -> tuple[QLineEdit, QAction]:
    """A covered field with the eye that uncovers it, wired to that field alone.

    The eye sits inside the field rather than beside it: a token needs the
    whole width of the page, and a button in the row would take some of it.
    Each field carries its own, so uncovering one token leaves the other as
    the page found it.
    """
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setPlaceholderText(placeholder)
    reveal = QAction(_eye_icon(crossed=True), REVEAL_TOKEN_TEXT, owner)
    reveal.setCheckable(True)
    reveal.toggled.connect(
        lambda revealed: _show_token_being_typed(field, reveal, revealed)
    )
    field.addAction(reveal, QLineEdit.ActionPosition.TrailingPosition)
    return field, reveal


def _show_token_being_typed(field: QLineEdit, reveal: QAction, revealed: bool) -> None:
    """Uncover a token, which is covered whenever the form is filled."""
    field.setEchoMode(
        QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password
    )
    reveal.setIcon(_eye_icon(crossed=not revealed))
    reveal.setText(HIDE_TOKEN_TEXT if revealed else REVEAL_TOKEN_TEXT)


def _switch_row(switch: ToggleSwitch, label: str) -> QHBoxLayout:
    """A switch with what it turns on written beside it."""
    row = QHBoxLayout()
    row.setSpacing(10)
    row.addWidget(switch)
    row.addWidget(QLabel(label))
    row.addStretch()
    return row


def _error_line() -> QLabel:
    """A line kept blank until there is something wrong to say on it."""
    line = QLabel()
    line.setObjectName("fieldError")
    line.setWordWrap(True)
    line.setVisible(False)
    return line


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
