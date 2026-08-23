from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click
from tiewtrade.ui.settings_dialogs import ask_about_clearing_database


def _answer(label: str, seen: list[QMessageBox]) -> Callable[[], None]:
    def answer() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        seen.append(dialog)
        click(next(button for button in dialog.buttons() if button.text() == label))

    return answer


def test_confirming_clears_the_database(qtbot: QtBot) -> None:
    seen: list[QMessageBox] = []
    QTimer.singleShot(0, _answer("Clear All", seen))

    answered = ask_about_clearing_database(None)

    assert answered is True
    assert len(seen) == 1
    assert seen[0].defaultButton() is not None
    assert seen[0].defaultButton().text() == "Cancel"


def test_cancelling_leaves_the_database_as_it_is(qtbot: QtBot) -> None:
    seen: list[QMessageBox] = []
    QTimer.singleShot(0, _answer("Cancel", seen))

    answered = ask_about_clearing_database(None)

    assert answered is False
    assert len(seen) == 1
