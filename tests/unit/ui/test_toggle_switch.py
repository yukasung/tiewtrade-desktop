from pytestqt.qtbot import QtBot

from tests.support.qt_interactions import click
from tiewtrade.ui.toggle_switch import (
    TRACK_OFF_COLOR,
    TRACK_ON_COLOR,
    ToggleSwitch,
)


def test_a_switch_starts_off_with_its_knob_at_the_off_end(qtbot: QtBot) -> None:
    switch = ToggleSwitch()
    qtbot.addWidget(switch)

    assert not switch.isChecked()
    assert switch.get_knob_position() == 0.0
    assert switch._track_color() == TRACK_OFF_COLOR


def test_clicking_a_switch_turns_it_on(qtbot: QtBot) -> None:
    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    switch.show()

    with qtbot.waitSignal(switch.toggled, timeout=1000):
        click(switch)

    assert switch.isChecked()
    assert switch._track_color() == TRACK_ON_COLOR


def test_clicking_it_again_turns_it_off(qtbot: QtBot) -> None:
    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    switch.show()

    click(switch)
    click(switch)

    assert not switch.isChecked()


def test_a_switch_set_from_stored_settings_is_already_in_position(
    qtbot: QtBot,
) -> None:
    """Opening a panel is not the trader flipping the switch.

    The knob is put where the setting says without sliding, so a panel that
    opens on a Bot already sending does not animate as though the setting
    had been changed the moment it was looked at.
    """
    switch = ToggleSwitch()
    qtbot.addWidget(switch)

    switch.setChecked(True)

    assert switch.get_knob_position() == 1.0


def test_a_switch_that_is_off_limits_says_so_by_going_flat(qtbot: QtBot) -> None:
    switch = ToggleSwitch()
    qtbot.addWidget(switch)

    switch.setEnabled(False)

    assert switch._track_color() not in (TRACK_ON_COLOR, TRACK_OFF_COLOR)
