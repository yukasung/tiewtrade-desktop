from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click
from tiewtrade.ui.settings_panel import (
    SAVE_LINE_BUSY_TEXT,
    SAVE_LINE_IDLE_TEXT,
    TOKEN_MISSING_TEXT,
    TOKEN_STORED_TEXT,
    SettingsPanel,
)


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


def test_saving_line_settings_emits_what_the_form_holds(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.line_enabled.setChecked(True)
    panel.line_target_id.setText("  U123  ")
    panel.line_access_token.setText("  token-value  ")

    with qtbot.waitSignal(
        panel.line_notifications_save_requested, timeout=1000
    ) as emitted:
        click(panel.save_line_button)

    assert emitted.args == [True, "U123", "token-value"]


def test_the_token_field_never_shows_what_it_holds(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    assert panel.line_access_token.echoMode() is QLineEdit.EchoMode.Password


def test_a_stored_token_is_described_rather_than_filled_in(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.show_line_notifications((True, "U123", True))

    assert panel.line_enabled.isChecked()
    assert panel.line_target_id.text() == "U123"
    assert panel.line_access_token.text() == ""
    assert panel.line_token_state.text() == TOKEN_STORED_TEXT


def test_settings_with_no_token_saved_say_so(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.line_access_token.setText("typed-but-not-saved")

    panel.show_line_notifications((False, "", False))

    assert not panel.line_enabled.isChecked()
    assert panel.line_access_token.text() == ""
    assert panel.line_token_state.text() == TOKEN_MISSING_TEXT


def test_set_busy_holds_the_save_button_too(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.set_busy(True)

    assert not panel.save_line_button.isEnabled()
    assert panel.save_line_button.text() == SAVE_LINE_BUSY_TEXT

    panel.set_busy(False)

    assert panel.save_line_button.isEnabled()
    assert panel.save_line_button.text() == SAVE_LINE_IDLE_TEXT
