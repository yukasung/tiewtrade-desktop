"""What is said while the market connection is away, and unsaid when it returns.

The other things this window puts up ask the trader something and wait for the
answer. This one tells them something and then takes it back on its own, so it
is an object with a show and a hide rather than a function that returns what
was clicked — and a test can read what it says without a window server.
"""

from typing import Protocol

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

CONNECTION_LOST_TEXT = "Reconnecting to Binance — prices are not current"


class ConnectionNotice(Protocol):
    """Says the connection is away, and stops saying it."""

    def show(self) -> None: ...

    def hide(self) -> None: ...


class ConnectionNoticeCard(QFrame):
    """The notice as a card sitting in the workspace, not a window over it.

    Built hidden and left out of the layout's space until shown, so the
    column above the bot control panel does not hold room open for a
    message that is not being said.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("connectionNoticeCard")
        self.label = QLabel(CONNECTION_LOST_TEXT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addWidget(self.label)
        self.hide()
