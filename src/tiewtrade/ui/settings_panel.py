"""Settings, hosted inside `SettingsDialog` rather than the Bot Control sidebar.

The sidebar has room for what the trader has to see at a glance while the Bot
runs, and that is not where a Binance API key, eventually joining the two
channels below, belongs. So this widget carries its own header and footer —
Settings / close above, Cancel / Save Settings below — and only the form
between them scrolls, the way the dialog's own spec asks for.

The LINE and Telegram fields are filled in from what is stored, the tokens
among them: a form that offers to change a token has to show which one it is
changing. They arrive covered and stay that way unless the trader asks to see
one, and each token is uncovered on its own — revealing the one being typed
should not put the other on screen beside it.

The two channels are a card each rather than one section with a choice in it,
because they are not a choice: a trader may run either, both, or neither, and
each is turned on by its own switch. Side by side while there is room for it,
and stacked the moment there is not — never squeezed to fit two across.

The Title at the top is the one value here that is read back out on screen
rather than sent anywhere: it is what the toolbar says, all of it, so someone
looking after several installations can tell which one the window in front of
them is.
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.ui.bot_title import bot_title_or_default
from tiewtrade.ui.toggle_switch import ToggleSwitch

CLEAR_DATABASE_BUSY_TEXT = "Clearing…"
CLEAR_DATABASE_IDLE_TEXT = "Clear All Database"
SAVE_SETTINGS_BUSY_TEXT = "Saving…"
SAVE_SETTINGS_IDLE_TEXT = "Save Settings"
CANCEL_TEXT = "Cancel"
CLOSE_BUTTON_TEXT = "✕"
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

# Below this width, a LINE card and a Telegram card sharing a row have no room
# left for a token once their own padding and the gap between them are taken
# out — so the two stack instead of reading as two narrow, cramped columns.
_TWO_COLUMN_BREAKPOINT = 560

type LineNotificationsReading = tuple[bool, str, str]
"""Turned on, who the messages go to, and the token they are sent with."""

type TelegramNotificationsReading = tuple[bool, str, str]
"""The same three things, for the chat a Telegram bot sends into."""


class SettingsPanel(QWidget):
    """The values and one-off actions that belong to the installation."""

    clear_database_requested = Signal()
    #: The whole page at once: the Title, then LINE, then Telegram.
    settings_save_requested = Signal(str, bool, str, str, bool, str, str)
    #: Asked for by the header's close button and by Cancel alike — neither
    #: means anything but "leave this open as it was found".
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")

        header, self.close_button = _header()
        self.close_button.clicked.connect(self.close_requested)

        # A remark, not an identity: nothing is bound to it and nothing reads
        # it but the toolbar, so it may be changed or emptied at any time,
        # while the Bot is running included.
        self.bot_title = QLineEdit()
        self.bot_title.setMaxLength(BOT_TITLE_MAX_LENGTH)
        self.bot_title.setPlaceholderText(BOT_TITLE_PLACEHOLDER)
        general_heading = QLabel("GENERAL")
        general_heading.setObjectName("eyebrow")
        general_form = QVBoxLayout()
        general_form.setContentsMargins(0, 0, 0, 0)
        general_form.setSpacing(12)
        general_form.addLayout(_field_row("Title", self.bot_title))

        notifications_heading = QLabel("NOTIFICATIONS")
        notifications_heading.setObjectName("eyebrow")

        line_card, line_widgets = _line_card(self)
        (
            self.line_enabled,
            self.line_target_id,
            self.line_access_token,
            self.reveal_token,
            self.line_token_state,
        ) = line_widgets

        telegram_card, telegram_widgets = _telegram_card(self)
        (
            self.telegram_enabled,
            self.telegram_chat_id,
            self.telegram_bot_token,
            self.reveal_telegram_token,
            self.telegram_token_state,
        ) = telegram_widgets

        columns = _NotificationsColumns(line_card, telegram_card)

        content = QWidget()
        content.setObjectName("settingsScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(16)
        content_layout.addWidget(general_heading)
        content_layout.addLayout(general_form)
        content_layout.addWidget(notifications_heading)
        content_layout.addWidget(columns)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.viewport().setObjectName("settingsScrollViewport")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        self.clear_database_button = QPushButton(CLEAR_DATABASE_IDLE_TEXT)
        self.clear_database_button.setObjectName("destructiveButton")
        self.clear_database_button.clicked.connect(self.clear_database_requested)

        self.cancel_button = QPushButton(CANCEL_TEXT)
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.close_requested)

        self.save_settings_button = QPushButton(SAVE_SETTINGS_IDLE_TEXT)
        self.save_settings_button.setObjectName("secondaryButton")
        self.save_settings_button.clicked.connect(self._save_settings)

        footer = QFrame()
        footer.setObjectName("settingsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 14, 20, 14)
        footer_layout.setSpacing(10)
        # Destructive on its own side: it answers to neither Cancel nor Save,
        # and a trader reading the row left to right should not mistake it
        # for a third way of leaving the form.
        footer_layout.addWidget(self.clear_database_button)
        footer_layout.addStretch()
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.save_settings_button)

        # Header and footer are fixed; only the form between them scrolls, so
        # Save Settings can never be pushed off the bottom by what is above it.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(scroll, 1)
        layout.addWidget(footer)

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


def _header() -> tuple[QFrame, QPushButton]:
    """The one row that never scrolls out of view above the form."""
    header = QFrame()
    header.setObjectName("settingsHeader")
    title = QLabel("Settings")
    title.setObjectName("pageTitle")
    close_button = QPushButton(CLOSE_BUTTON_TEXT)
    close_button.setObjectName("dialogCloseButton")
    close_button.setToolTip("Close")
    layout = QHBoxLayout(header)
    layout.setContentsMargins(20, 16, 16, 16)
    layout.addWidget(title)
    layout.addStretch()
    layout.addWidget(close_button)
    return header, close_button


class _NotificationsColumns(QWidget):
    """Two cards, side by side while there is room and stacked once there is not.

    A `QBoxLayout` already carries both directions a card can be laid out in,
    so switching between them is a matter of asking it to point the other way
    rather than tearing the cards out of one layout to add them to another.
    """

    def __init__(
        self, line_card: QWidget, telegram_card: QWidget, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(16)
        self._box.addWidget(line_card, 1)
        self._box.addWidget(telegram_card, 1)

    def resizeEvent(self, event: object) -> None:  # noqa: ARG002 - Qt's own signature
        wide = self.width() >= _TWO_COLUMN_BREAKPOINT
        direction = (
            QBoxLayout.Direction.LeftToRight
            if wide
            else QBoxLayout.Direction.TopToBottom
        )
        if self._box.direction() != direction:
            self._box.setDirection(direction)


type _ChannelWidgets = tuple[ToggleSwitch, QLineEdit, QLineEdit, QAction, QLabel]


def _line_card(owner: QWidget) -> tuple[QFrame, _ChannelWidgets]:
    heading = QLabel("LINE NOTIFICATIONS")
    heading.setObjectName("eyebrow")
    # A switch rather than a tick: this setting is not a fact about the
    # Session, it is something left running or left off.
    enabled = ToggleSwitch()
    enabled_row = _switch_row(enabled, "Send a LINE message")
    target_id = QLineEdit()
    target_id.setPlaceholderText("User or group ID")
    access_token, reveal = _covered_field(owner, TOKEN_MISSING_PLACEHOLDER)
    # Blank unless something is wrong: what a trader has to be told about
    # these settings is that they could not be read, not that they were.
    token_state = _error_line()

    form = QVBoxLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(12)
    form.addLayout(_field_row("Send to", target_id))
    form.addLayout(_field_row("Access token", access_token))

    card = QFrame()
    card.setObjectName("card")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(16, 16, 16, 16)
    card_layout.setSpacing(12)
    card_layout.addWidget(heading)
    card_layout.addLayout(enabled_row)
    card_layout.addLayout(form)
    card_layout.addWidget(token_state)
    return card, (enabled, target_id, access_token, reveal, token_state)


def _telegram_card(owner: QWidget) -> tuple[QFrame, _ChannelWidgets]:
    heading = QLabel("TELEGRAM NOTIFICATIONS")
    heading.setObjectName("eyebrow")
    enabled = ToggleSwitch()
    enabled_row = _switch_row(enabled, "Send a Telegram message")
    # A chat id, not a username: a bot can only send into a chat it has been
    # added to, and that chat is named by a number the trader reads off their
    # own bot rather than by the handle they see in the app.
    chat_id = QLineEdit()
    chat_id.setPlaceholderText("Chat, group or channel ID")
    bot_token, reveal = _covered_field(owner, TELEGRAM_TOKEN_MISSING_PLACEHOLDER)
    token_state = _error_line()

    form = QVBoxLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(12)
    form.addLayout(_field_row("Send to", chat_id))
    form.addLayout(_field_row("Bot token", bot_token))

    card = QFrame()
    card.setObjectName("card")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(16, 16, 16, 16)
    card_layout.setSpacing(12)
    card_layout.addWidget(heading)
    card_layout.addLayout(enabled_row)
    card_layout.addLayout(form)
    card_layout.addWidget(token_state)
    return card, (enabled, chat_id, bot_token, reveal, token_state)


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
