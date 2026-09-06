"""The one card the workspace says a standing message on, whatever it is about.

Two things now stand beside the Bot and tell the trader something without
being asked — a connection that has gone, and a Cooldown holding the next
Entry back. They are one shape: a line of text that appears when it becomes
true and takes itself back when it stops being true, holding no room open in
between. The shape is written once here; what differs is the words and how
loudly they are said.

They are separate cards rather than one line shared, because both can be true
at the same moment — a feed dropping during a Cooldown is not a reason to stop
saying either — and a single line would have to pick one and hide the other.
"""

from enum import Enum

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class NoticeLevel(Enum):
    """How loudly a notice is said, which the stylesheet reads as colour.

    A fault the trader may have to act on does not look like the strategy
    working as designed, or a Session doing exactly what it was told reads
    on screen as something going wrong.
    """

    WARNING = "warning"
    INFO = "info"


class NoticeCard(QFrame):
    """A standing message as a card in the workspace, not a window over it.

    Built hidden and left out of the layout's space until there is something
    to say, so the column beside the chart does not keep a gap where a
    message would have been.
    """

    def __init__(
        self,
        level: NoticeLevel,
        *,
        text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("noticeCard")
        # Read by the stylesheet, so it is set before the card is ever shown
        # and never changes: a card is one kind of notice for its whole life.
        self.setProperty("level", level.value)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addWidget(self.label)
        self.hide()

    def say(self, text: str) -> None:
        """Put these words up, or take the card back when there are none."""
        if not text:
            self.hide()
            return
        self.label.setText(text)
        self.show()
