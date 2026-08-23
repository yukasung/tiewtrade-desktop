"""The question the Settings panel puts, and nothing about what is done with it.

Same shape as `position_dialogs.py`, and built by the same helper.
"""

from PySide6.QtWidgets import QWidget

from tiewtrade.ui.destructive_question import ask_before_acting


def ask_about_clearing_database(parent: QWidget | None) -> bool:
    """Ask before every Basket, Fill, and Session record is deleted for good."""
    return ask_before_acting(
        parent,
        title="Clear All Database",
        question="Delete every Basket, Fill, and Session record?",
        detail=(
            "This clears Position History and Trade History for good. It does not "
            "touch the database file or its schema — the app keeps working, with "
            "nothing recorded yet.\n\n"
            "Cancel leaves the database as it is."
        ),
        confirm_label="Clear All",
    )
