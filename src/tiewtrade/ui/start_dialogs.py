"""The questions a Start puts, and nothing about what is done with the answer.

Each returns what was answered rather than calling back into the window, so
the wording and the button roles can be read — and tested — without the flow
they belong to, and the flow can be read without the wording.
"""

from enum import StrEnum

from PySide6.QtWidgets import QMessageBox, QWidget


class PositionAtTarget(StrEnum):
    """What to do with a Position the price has already carried to its target."""

    SELL_AT_TARGET = "sell_at_target"
    CLOSE_AT_MARKET = "close_at_market"
    CANCEL = "cancel"


class ReplaceSession(StrEnum):
    """What to do with the Position of a Session being replaced."""

    CLOSE_AT_MARKET = "close_at_market"
    KEEP_AND_RESUME = "keep_and_resume"


def ask_about_a_position_at_its_target(
    parent: QWidget | None, sells: str
) -> PositionAtTarget:
    """Ask about the sale that is waiting, not about replacing a Session.

    A Position whose target the price has already reached is what the trader
    is deciding about, so that is what the question is about. The two ways out
    of it are named: selling at the targets the Entries were given, or selling
    the lot at the price now — which is a different trade, and no longer
    something a stray Enter can pick.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Take Profit reached")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Sell this Position at its Take Profit?")
    dialog.setInformativeText(
        f"{sells}\n\n"
        "Close at Market sells the whole Position at the price now instead, "
        "and starts a new Paper Bot with the values on this form.\n\n"
        "Cancel leaves the Position open and the Bot stopped."
    )
    sell_button = dialog.addButton(
        "Sell at Take Profit", QMessageBox.ButtonRole.AcceptRole
    )
    close_button = dialog.addButton(
        "Close at Market", QMessageBox.ButtonRole.DestructiveRole
    )
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    clicked = dialog.clickedButton()
    if clicked is sell_button:
        return PositionAtTarget.SELL_AT_TARGET
    if clicked is close_button:
        return PositionAtTarget.CLOSE_AT_MARKET
    return PositionAtTarget.CANCEL


def ask_about_replacing_the_session(parent: QWidget | None) -> ReplaceSession:
    """Ask which Session is wanted, since only one can hold the Position."""
    answer = QMessageBox.question(
        parent,
        "Close Paper Position?",
        "Yes closes all Paper positions at the latest market price and "
        "starts a new Paper Bot with the values on this form.\n\n"
        "No leaves them open and starts the existing Paper Session again.",
        QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
        QMessageBox.StandardButton.Yes,
    )
    if answer == QMessageBox.StandardButton.Yes:
        return ReplaceSession.CLOSE_AT_MARKET
    return ReplaceSession.KEEP_AND_RESUME
