"""The question the Settings panel puts, and nothing about what is done with it.

It returns what was answered rather than calling back into the window, same
shape as `position_dialogs.py`.
"""

from PySide6.QtWidgets import QMessageBox, QWidget


def ask_about_clearing_database(parent: QWidget | None) -> bool:
    """Ask before every Basket, Fill, and Session record is deleted for good."""
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Clear All Database")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Delete every Basket, Fill, and Session record?")
    dialog.setInformativeText(
        "This clears Position History and Trade History for good. It does not "
        "touch the database file or its schema — the app keeps working, with "
        "nothing recorded yet.\n\n"
        "Cancel leaves the database as it is."
    )
    clear_button = dialog.addButton("Clear All", QMessageBox.ButtonRole.DestructiveRole)
    cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    return dialog.clickedButton() is clear_button
