"""What is said while the market connection is away, and unsaid when it returns.

The other dialogs in this window ask the trader something and wait for the
answer. This one tells them something and then takes it back on its own, so it
is an object with a show and a hide rather than a function that returns what
was clicked — and a test can read what it says without a window server.
"""

from typing import Protocol

from PySide6.QtWidgets import QMessageBox, QWidget

CONNECTION_LOST_TITLE = "Connection Lost"
CONNECTION_LOST_TEXT = "Reconnecting to Binance — prices are not current"
CONNECTION_LOST_DETAIL = (
    "The connection dropped and is being retried on its own, for as long as "
    "it takes. Nothing needs to be done here.\n\n"
    "Candles missed during the outage are fetched back when the connection "
    "returns. A backfilled candle still closes a Basket, but it never opens "
    "an Entry, so no trade is taken on a price the Bot learned late.\n\n"
    "This message closes itself once prices are current again."
)


class ConnectionNotice(Protocol):
    """Says the connection is away, and stops saying it."""

    def show(self) -> None: ...

    def hide(self) -> None: ...


class ConnectionNoticeDialog:
    """The notice as a real dialog, at most one of it at a time.

    Opened rather than executed: what raises it is a runtime snapshot
    arriving on a queued signal, not a click, and a blocking dialog there
    would stop the window from reading the snapshot that clears it.
    """

    def __init__(self, parent: QWidget | None) -> None:
        self._parent = parent
        self._dialog: QMessageBox | None = None

    def show(self) -> None:
        if self._dialog is not None:
            return
        dialog = QMessageBox(self._parent)
        dialog.setWindowTitle(CONNECTION_LOST_TITLE)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(CONNECTION_LOST_TEXT)
        dialog.setInformativeText(CONNECTION_LOST_DETAIL)
        dialog.addButton(QMessageBox.StandardButton.Ok)
        # A reader who closes this has read it; the connection coming back
        # must not be an occasion to say it again.
        dialog.finished.connect(self._forget)
        dialog.open()
        self._dialog = dialog

    def hide(self) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.done(0)

    def _forget(self) -> None:
        self._dialog = None
