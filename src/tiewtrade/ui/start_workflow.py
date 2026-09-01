"""What pressing Start commits the trader to, and the question it puts first.

A Position left open goes on being marked while nothing runs, so the price can
reach an Entry's target with no Bot to act on it. The first live tick after a
Start sells every such Entry at once, at the target it was given rather than at
the price on screen — and there is no way back from a Fill. Short of a target it
sells nothing, but it is still this press that hands an open Position back to a
Bot, so that is said too.

So Start is never one thing. It reads the Basket again first, because the copy
on screen is only as new as the last time something asked, and a question about
a Position has to be put to the Position that is there now. Then it says what
that reading came to, or starts without asking where there was nothing to ask
about.

`start_bot` is called from exactly one place here, whichever question preceded
it — the routes that put no question of their own come through the same door.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtWidgets import QMessageBox, QWidget

from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.ui.open_position_workflow import OpenPositionWorkflow
from tiewtrade.ui.take_profit_confirmation import UNREADABLE_POSITION_MESSAGE

# A question put to the trader, answered by calling the confirmation it is
# handed. The same shape the Stop confirmation takes, so both can be given a
# plain callable in a test rather than a dialog to click.
type TakeProfitConfirmation = Callable[[str, Callable[[], None]], None]

CurrentSession = Callable[[], ConfiguredPaperSession | None]


class StartWorkflow(QObject):
    """Reads the Position again, asks about it where it matters, then starts."""

    def __init__(
        self,
        *,
        position: OpenPositionWorkflow,
        current_session: CurrentSession,
        start_bot: Callable[[], None],
        dialog_parent: QWidget,
        confirmation: TakeProfitConfirmation | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._position = position
        self._current_session = current_session
        self._start_bot = start_bot
        self._dialog_parent = dialog_parent
        self._confirmation = confirmation or self._open_confirmation
        self._pending = False
        # Held so the dialog outlives the call that opened it; nothing reads it.
        self._dialog: QMessageBox | None = None

    @Slot()
    def requested(self) -> None:
        """Start the Bot, asking first about any Position it would run on.

        The Basket is read again before it is asked about, which is the whole
        reason the answer arrives in a callback rather than on this line.
        """
        if self._pending:
            return
        self._position.read_then(self._current_session(), self._ask_before_starting)

    def start_now(self) -> None:
        """The one line that starts the Bot, whichever question preceded it."""
        self._start_bot()

    def sells_on_start_message(self) -> str | None:
        """What a Start would sell now, asked of the Position that is held.

        A Futures Session never takes a Basket back, so it answers the same
        way as having none: there is nothing to put a question about.
        """
        return self._said_or_asked_anyway(self._position.sells_on_start_message())

    def position_at_target_message(self) -> str | None:
        """What the held Position has reached, in the words the dialog puts it.

        Read from the same Basket and price as `sells_on_start_message`, which
        is what decided a question was worth putting at all.
        """
        return self._said_or_asked_anyway(self._position.position_at_target_message())

    def position_held_message(self) -> str | None:
        """What the Position stands at, in the words the dialog puts it in.

        Read from the same Basket and price as the other two, which is what
        decided a question was worth putting at all.
        """
        return self._said_or_asked_anyway(self._position.position_held_message())

    def _said_or_asked_anyway(self, message: str | None) -> str | None:
        """The reading itself, or the reason there is none to be had.

        Silence has two meanings — nothing is at its target, and nothing can
        be told yet — and only one of them makes it safe to start without
        asking. A Basket still being read, or one with no price yet, took the
        question away and sold a Position at a target the price had left.
        """
        if message is not None:
            return message
        if self._position.what_a_start_sells_is_unknown():
            return UNREADABLE_POSITION_MESSAGE
        return None

    def _ask_before_starting(self) -> None:
        """Put the question the reading turned out to be worth, or none."""
        message = self.sells_on_start_message() or self.position_held_message()
        if message is None:
            self.start_now()
            return
        if self._pending:
            return
        self._pending = True
        self._confirmation(message, self._confirmed)

    def _confirmed(self) -> None:
        self._pending = False
        self.start_now()

    def _open_confirmation(self, message: str, confirm: Callable[[], None]) -> None:
        dialog = QMessageBox(self._dialog_parent)
        dialog.setWindowTitle("Open Position")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText("Start the Bot on the Position it holds?")
        dialog.setInformativeText(message)
        start_button = dialog.addButton("Start Bot", QMessageBox.ButtonRole.AcceptRole)
        cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        # The confirmed answer is the one that cannot be taken back, so the
        # key an impatient hand hits is the one that changes nothing.
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)

        def finish(_result: int) -> None:
            if dialog.clickedButton() is start_button:
                confirm()
            else:
                self._pending = False
            dialog.deleteLater()

        dialog.finished.connect(finish)
        self._dialog = dialog
        dialog.open()
