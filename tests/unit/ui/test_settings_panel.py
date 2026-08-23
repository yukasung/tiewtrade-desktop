from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click
from tiewtrade.ui.settings_panel import SettingsPanel


def test_clear_database_button_is_always_enabled(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.show()

    assert panel.clear_database_button.isEnabled()


def test_clicking_the_button_emits_clear_database_requested(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    with qtbot.waitSignal(panel.clear_database_requested, timeout=1000):
        click(panel.clear_database_button)


def test_clicking_back_emits_back_requested(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    with qtbot.waitSignal(panel.back_requested, timeout=1000):
        click(panel.back_button)


def test_set_busy_disables_the_button_and_changes_its_label(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.set_busy(True)

    assert not panel.clear_database_button.isEnabled()
    assert panel.clear_database_button.text() == "Clearing…"

    panel.set_busy(False)

    assert panel.clear_database_button.isEnabled()
    assert panel.clear_database_button.text() == "Clear All Database"
