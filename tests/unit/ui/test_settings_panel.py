from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click
from tiewtrade.ui.settings_panel import (
    _EYE_ICON_SIZE,
    BOT_TITLE_MAX_LENGTH,
    SAVE_SETTINGS_BUSY_TEXT,
    SAVE_SETTINGS_IDLE_TEXT,
    TOKEN_MISSING_PLACEHOLDER,
    TOKEN_UNREADABLE_TEXT,
    SettingsPanel,
    _eye_icon,
)


def _looks_like(icon: QIcon, *, crossed: bool) -> bool:
    size = QSize(_EYE_ICON_SIZE, _EYE_ICON_SIZE)
    return (
        icon.pixmap(size).toImage() == _eye_icon(crossed=crossed).pixmap(size).toImage()
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


def test_the_line_fields_are_stated_under_their_labels_and_fill_the_width(
    qtbot: QtBot,
) -> None:
    """A token is far longer than a field sharing its row with a label.

    Side by side, the label took the width the value needed and the token
    scrolled past the edge of a field a few characters wide.
    """
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.resize(600, 500)
    panel.show()
    qtbot.waitUntil(panel.line_target_id.isVisible)

    for field, text in (
        (panel.line_target_id, "Send to"),
        (panel.line_access_token, "Access token"),
    ):
        label = next(
            widget for widget in panel.findChildren(QLabel) if widget.text() == text
        )
        assert label.y() < field.y()
        # Whatever room the panel has, less its own margins, is the field's.
        assert field.width() > panel.width() - 60


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


def test_saving_emits_everything_the_form_holds(qtbot: QtBot) -> None:
    """One button, one save: the page is kept the way it is read."""
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.bot_title.setText("  Khun Somchai  ")
    panel.line_enabled.setChecked(True)
    panel.line_target_id.setText("  U123  ")
    panel.line_access_token.setText("  token-value  ")

    with qtbot.waitSignal(panel.settings_save_requested, timeout=1000) as emitted:
        click(panel.save_settings_button)

    assert emitted.args == ["Khun Somchai", True, "U123", "token-value"]


def test_the_bot_title_is_filled_in_from_what_is_stored(qtbot: QtBot) -> None:
    """A form that offers to change a title has to show the one it changes."""
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.show_bot_title("Khun Somchai")

    assert panel.bot_title.text() == "Khun Somchai"


def test_an_untitled_installation_offers_the_name_to_start_from(
    qtbot: QtBot,
) -> None:
    """Typed into the field, not suggested behind it: it is there to be edited.

    A trader who wants their own title has to be able to take this one out,
    and a placeholder is nothing to take out.
    """
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.show_bot_title("")

    assert panel.bot_title.text() == "TiewTrade"


def test_the_bot_title_stops_at_what_a_toolbar_can_hold(qtbot: QtBot) -> None:
    """Stopped at the last character, rather than trimmed behind their back."""
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.bot_title.setText("x" * (BOT_TITLE_MAX_LENGTH + 20))

    assert len(panel.bot_title.text()) == BOT_TITLE_MAX_LENGTH


def test_the_token_field_starts_as_dots(qtbot: QtBot) -> None:
    """Typing a token in front of whoever is nearby is nobody's default."""
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    assert panel.line_access_token.echoMode() is QLineEdit.EchoMode.Password
    assert not panel.reveal_token.isChecked()


def test_the_eye_is_struck_through_while_the_token_is_hidden(
    qtbot: QtBot,
) -> None:
    """The button says what a click does, not what the field is doing.

    A struck-through eye is the one every other password field offers to
    open, so a covered token wears it and an uncovered one does not.
    """
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    assert _looks_like(panel.reveal_token.icon(), crossed=True)

    panel.reveal_token.trigger()

    assert _looks_like(panel.reveal_token.icon(), crossed=False)

    panel.reveal_token.trigger()

    assert _looks_like(panel.reveal_token.icon(), crossed=True)


def test_the_token_being_typed_can_be_read_back_and_hidden_again(
    qtbot: QtBot,
) -> None:
    """A token is long enough to mistype, and dots say nothing about where."""
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.line_access_token.setText("token-value")

    panel.reveal_token.trigger()

    assert panel.line_access_token.echoMode() is QLineEdit.EchoMode.Normal

    panel.reveal_token.trigger()

    assert panel.line_access_token.echoMode() is QLineEdit.EchoMode.Password


def test_reading_the_settings_back_covers_the_token_field_again(
    qtbot: QtBot,
) -> None:
    """A revealed field left open outlives the reason it was opened.

    Saving empties the field and reads the settings back, and a page returned
    to later should not still be the one that was being typed into.
    """
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.line_access_token.setText("token-value")
    panel.reveal_token.trigger()

    panel.show_line_notifications((True, "U123", "token-value"))

    assert panel.line_access_token.echoMode() is QLineEdit.EchoMode.Password
    assert not panel.reveal_token.isChecked()


def test_a_stored_token_is_filled_in_behind_dots(qtbot: QtBot) -> None:
    """The form shows what is stored, the way the recipient beside it does.

    A field that only ever said a token was there left a trader no way to
    check which one, and no way to edit the one they had.
    """
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.show_line_notifications((True, "U123", "token-value"))

    assert panel.line_enabled.isChecked()
    assert panel.line_target_id.text() == "U123"
    assert panel.line_access_token.text() == "token-value"
    assert panel.line_access_token.echoMode() is QLineEdit.EchoMode.Password


def test_settings_with_no_token_saved_say_so(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.line_access_token.setText("typed-but-not-saved")

    panel.show_line_notifications((False, "", ""))

    assert not panel.line_enabled.isChecked()
    assert panel.line_access_token.text() == ""
    assert panel.line_access_token.placeholderText() == TOKEN_MISSING_PLACEHOLDER


def test_settings_nobody_could_read_say_so_where_they_were_typed(
    qtbot: QtBot,
) -> None:
    """An empty form and a form nobody could read mean opposite things.

    One is a Bot that will send nothing because it was never set up, the
    other a Bot that may well send and cannot say what it is sending with.
    Nothing else on the page says anything, so this line has to.
    """
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.show()
    assert not panel.line_token_state.isVisible()

    panel.say_line_notifications_unreadable()

    assert panel.line_token_state.isVisible()
    assert panel.line_token_state.text() == TOKEN_UNREADABLE_TEXT

    panel.show_line_notifications((False, "", ""))

    assert not panel.line_token_state.isVisible()


def test_set_busy_holds_the_save_button_too(qtbot: QtBot) -> None:
    panel = SettingsPanel()
    qtbot.addWidget(panel)

    panel.set_busy(True)

    assert not panel.save_settings_button.isEnabled()
    assert panel.save_settings_button.text() == SAVE_SETTINGS_BUSY_TEXT

    panel.set_busy(False)

    assert panel.save_settings_button.isEnabled()
    assert panel.save_settings_button.text() == SAVE_SETTINGS_IDLE_TEXT
