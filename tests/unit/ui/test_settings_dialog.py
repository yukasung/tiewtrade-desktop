from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click
from tiewtrade.ui.settings_dialog import CLEAR_DATABASE_HINT, SettingsDialog


def test_clear_database_button_is_enabled_with_no_session_loaded(qtbot: QtBot) -> None:
    dialog = SettingsDialog(can_clear_database=True)
    qtbot.addWidget(dialog)

    assert dialog.clear_database_button.isEnabled()


def test_clear_database_button_is_disabled_with_a_session_loaded(qtbot: QtBot) -> None:
    dialog = SettingsDialog(can_clear_database=False)
    qtbot.addWidget(dialog)

    assert not dialog.clear_database_button.isEnabled()
    hints = [label.text() for label in dialog.findChildren(QLabel)]
    assert CLEAR_DATABASE_HINT in hints


def test_clicking_the_enabled_button_emits_clear_database_requested(
    qtbot: QtBot,
) -> None:
    dialog = SettingsDialog(can_clear_database=True)
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.clear_database_requested, timeout=1000):
        click(dialog.clear_database_button)


def test_set_busy_disables_the_button_and_changes_its_label(qtbot: QtBot) -> None:
    dialog = SettingsDialog(can_clear_database=True)
    qtbot.addWidget(dialog)

    dialog.set_busy(True)

    assert not dialog.clear_database_button.isEnabled()
    assert dialog.clear_database_button.text() == "Clearing…"

    dialog.set_busy(False)

    assert dialog.clear_database_button.isEnabled()
    assert dialog.clear_database_button.text() == "Clear All Database"
