"""The one shape every question that precedes an irreversible action takes.

Each caller keeps its own wording — what is about to happen, and why it is
being asked at all — and this holds the part none of them should be deciding
separately: the destructive answer is a button of its own, and the answer that
does nothing is the one a stray Enter or Escape finds.
"""

from PySide6.QtWidgets import QMessageBox, QWidget

CANCEL_LABEL = "Cancel"


def ask_before_acting(
    parent: QWidget | None,
    *,
    title: str,
    question: str,
    detail: str,
    confirm_label: str,
) -> bool:
    """Put the question, and answer whether the destructive button was the one.

    Returns rather than calling back, so the wording and the button roles can
    be read — and tested — without the flow they belong to, and the flow can be
    read without the wording.
    """
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(question)
    dialog.setInformativeText(detail)
    confirm_button = dialog.addButton(
        confirm_label, QMessageBox.ButtonRole.DestructiveRole
    )
    cancel_button = dialog.addButton(CANCEL_LABEL, QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    return dialog.clickedButton() is confirm_button
