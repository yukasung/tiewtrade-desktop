"""What is said while the market connection is away, and unsaid when it returns.

The other things this window puts up ask the trader something and wait for the
answer. This one tells them something and then takes it back on its own, so it
is an object with a show and a hide rather than a function that returns what
was clicked — and a test can read what it says without a window server.

The card it is said on is the workspace's own notice card, shared with the
other standing message beside the Bot. What belongs here is the one thing this
notice says: the words, and that they are a fault rather than the strategy
going to plan.
"""

from typing import Protocol

from PySide6.QtWidgets import QWidget

from tiewtrade.ui.notice_card import NoticeCard, NoticeLevel

CONNECTION_LOST_TEXT = "Reconnecting to Binance — prices are not current"


class ConnectionNotice(Protocol):
    """Says the connection is away, and stops saying it."""

    def show(self) -> None: ...

    def hide(self) -> None: ...


class ConnectionNoticeCard(NoticeCard):
    """The notice as a card sitting in the workspace, not a window over it.

    It carries one sentence for its whole life — there is only one thing a
    lost connection has to say — so the words are set once at build time and
    showing the card is the whole of raising it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(NoticeLevel.WARNING, text=CONNECTION_LOST_TEXT, parent=parent)
