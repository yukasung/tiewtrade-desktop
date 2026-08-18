"""The questions a Start puts, and nothing about what is done with the answer.

Each returns what was answered rather than calling back into the window, so
the wording and the button roles can be read — and tested — without the flow
they belong to, and the flow can be read without the wording.
"""

from enum import StrEnum

from PySide6.QtWidgets import QMessageBox, QWidget


class PositionAtTarget(StrEnum):
    """What to do with a Position the price has already carried to its target."""

    CLOSE_AT_MARKET = "close_at_market"
    CANCEL = "cancel"


class PositionOnStart(StrEnum):
    """What to do with a Position a Start has met, at its target or not."""

    START_ON_IT = "start_on_it"
    CLOSE_AT_MARKET = "close_at_market"
    CANCEL = "cancel"


def ask_about_a_position_at_its_target(
    parent: QWidget | None, reached: str
) -> PositionAtTarget:
    """Ask about the sale that is waiting, not about replacing a Session.

    A Position whose target the price has already reached is what the trader
    is deciding about, so that is what the question is about. Only one trade
    is on offer — selling the lot at the price now — because selling at a
    target the price has already left is not a sale worth keeping a button
    for. The one that trades nothing is what a stray Enter still picks.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Take Profit reached")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Close this Position at the market price?")
    dialog.setInformativeText(
        f"{reached}\n\n"
        "Close at Market sells the whole Position at the price now, "
        "and starts a new Paper Bot with the values on this form.\n\n"
        "Cancel leaves the Position open and the Bot stopped."
    )
    close_button = dialog.addButton(
        "Close at Market", QMessageBox.ButtonRole.DestructiveRole
    )
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    if dialog.clickedButton() is close_button:
        return PositionAtTarget.CLOSE_AT_MARKET
    return PositionAtTarget.CANCEL


def ask_about_the_open_position(
    parent: QWidget | None, holding: str, at_target: str | None = None
) -> PositionOnStart:
    """Ask what the Start is to do with the Position that is already there.

    Running the Session on trades nothing where nothing has reached a target,
    which is why this press used to go through without a question. It is still
    the one that puts an open Position back under a Bot, and each answer is
    named after what it does to that Position rather than after Yes and No.
    Labelled Yes and No, with Yes the default, this question sold two Positions
    at the market price for a trader who only meant to press Start twice.

    Where part of the Position has reached a target, running on is no longer
    free — that part sells at once, at the price it was promised rather than
    the one on screen — so Start Bot says so. Withholding the answer instead
    left closing the lot at the market price as the only way forward, which
    sold the Entries that were still holding too.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Open Position")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Start the Bot on the Position it holds?")
    if at_target is None:
        start_line = (
            "Start Bot runs the Paper Session that is already there, position "
            "and all. The values on this form are not used."
        )
    else:
        start_line = (
            "Start Bot runs the Paper Session that is already there. What has "
            "reached its Take Profit is sold at once at the target it was "
            "given, not at the price now, and the rest is run on. The values "
            "on this form are not used."
        )
    dialog.setInformativeText(
        f"{holding}{'' if at_target is None else f' {at_target}'}\n\n"
        f"{start_line}\n\n"
        "Close at Market sells the whole Position at the price now, and starts "
        "a new Paper Bot with the values on this form.\n\n"
        "Cancel leaves the Position open and the Bot stopped."
    )
    start_button = dialog.addButton("Start Bot", QMessageBox.ButtonRole.AcceptRole)
    close_button = dialog.addButton(
        "Close at Market", QMessageBox.ButtonRole.DestructiveRole
    )
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    # The answer that trades nothing is the one a stray Enter or Escape finds.
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    clicked = dialog.clickedButton()
    if clicked is start_button:
        return PositionOnStart.START_ON_IT
    if clicked is close_button:
        return PositionOnStart.CLOSE_AT_MARKET
    return PositionOnStart.CANCEL
