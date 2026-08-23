"""The question a Position tab puts, and nothing about what is done with it.

It returns what was answered rather than calling back into the window, so the
wording and the button roles can be read — and tested — without the flow they
belong to, and the flow can be read without the wording.
"""

from PySide6.QtWidgets import QMessageBox, QWidget


def ask_about_selling_one_entry(parent: QWidget | None, entry_number: int) -> bool:
    """Ask before one Entry goes at the price the market is at.

    A Fill has no way back, and this button sits in a row that redraws under
    the hand on every tick, so the Entry it would sell is named in the
    question. The answer that trades nothing is what a stray Enter finds.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Close Position")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(f"Sell Entry {entry_number} at the market price?")
    dialog.setInformativeText(
        f"Market sells Entry {entry_number} at the price now, not at the Take "
        "Profit it was given. The rest of the Position is left as it is, and "
        "the Bot goes on running.\n\n"
        "Cancel leaves the Entry open."
    )
    sell_button = dialog.addButton("Market", QMessageBox.ButtonRole.DestructiveRole)
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    return dialog.clickedButton() is sell_button


def ask_about_closing_all_at_market(parent: QWidget | None) -> bool:
    """Ask before every remaining Entry goes at the price the market is at.

    A Fill has no way back, and this closes the whole Position in one Fill —
    not just the row under the hand — so the question says what it does
    instead of what it is beside. The answer that trades nothing is what a
    stray Enter finds.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Close All at Market")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Sell the whole Position at the market price now?")
    dialog.setInformativeText(
        "Market sells every remaining Entry at the price now, not at the "
        "Take Profit each was given, and closes the Basket.\n\n"
        "Cancel leaves the Position open."
    )
    sell_button = dialog.addButton("Close All", QMessageBox.ButtonRole.DestructiveRole)
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    return dialog.clickedButton() is sell_button


def ask_about_cancelling_cooldown(parent: QWidget | None) -> bool:
    """Ask before a Cooldown the strategy itself set is lifted.

    A Cooldown does not lapse, so lifting it is the trader stepping around a
    risk policy rather than hurrying one along — the question says so in as
    many words.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Cancel Cooldown")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Lift this Cooldown and open a new Entry now?")
    dialog.setInformativeText(
        "This Basket completed an Entry Pair, so the strategy is holding it "
        "back from taking another Entry. Lifting the Cooldown lets one open "
        "immediately. The next Pair it completes is held back again.\n\n"
        "Cancel leaves the Cooldown as it is."
    )
    waive_button = dialog.addButton(
        "Cancel Cooldown", QMessageBox.ButtonRole.DestructiveRole
    )
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    return dialog.clickedButton() is waive_button
