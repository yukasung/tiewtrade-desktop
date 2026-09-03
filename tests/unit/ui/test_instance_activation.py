from pathlib import Path

from PySide6.QtNetwork import QLocalServer
from pytestqt.qtbot import QtBot

from tiewtrade.ui.instance_activation import (
    InstanceActivation,
    activation_name_for,
)

# Long enough that a loaded machine still answers, short enough that a test
# which is meant to find nobody home does not sit here.
_ANSWER_TIMEOUT_MS = 2_000


def test_the_name_is_the_same_one_every_launch(tmp_path: Path) -> None:
    """Two launches of one installation have to arrive at one name.

    The name is the whole of how the second process finds the first, so a
    name derived afresh each run would leave every launch talking to itself.
    """
    database = tmp_path / "tiewtrade.sqlite3"

    assert activation_name_for(database) == activation_name_for(database)


def test_two_installations_do_not_share_a_name(tmp_path: Path) -> None:
    """A checkout's own database must not ring the installed app's window."""
    installed = tmp_path / "installed" / "tiewtrade.sqlite3"
    checkout = tmp_path / "checkout" / "tiewtrade.sqlite3"

    assert activation_name_for(installed) != activation_name_for(checkout)


def test_the_name_is_something_a_local_socket_accepts(tmp_path: Path) -> None:
    """Qt puts the name on a filesystem path or a pipe name, so it may not
    carry a separator or a character a path would read as one."""
    name = activation_name_for(tmp_path / "tiewtrade.sqlite3")

    assert name
    assert "/" not in name
    assert "\\" not in name


def test_nobody_listening_is_not_an_answer(tmp_path: Path) -> None:
    """The whole reason the caller asks: is there a window to hand over to?"""
    asking = InstanceActivation(activation_name_for(tmp_path / "tiewtrade.sqlite3"))

    assert asking.ask_the_running_window(timeout_ms=_ANSWER_TIMEOUT_MS) is False


def test_a_request_reaches_the_window(qtbot: QtBot, tmp_path: Path) -> None:
    name = activation_name_for(tmp_path / "tiewtrade.sqlite3")
    asked_for = []
    listening = InstanceActivation(name)
    assert listening.listen(lambda: asked_for.append("present")) is True

    try:
        assert (
            InstanceActivation(name).ask_the_running_window(
                timeout_ms=_ANSWER_TIMEOUT_MS
            )
            is True
        )
        qtbot.waitUntil(lambda: asked_for == ["present"], timeout=_ANSWER_TIMEOUT_MS)
    finally:
        listening.close()


def test_every_request_reaches_the_window(qtbot: QtBot, tmp_path: Path) -> None:
    """A trader who clicks the icon three times is asking three times."""
    name = activation_name_for(tmp_path / "tiewtrade.sqlite3")
    asked_for = []
    listening = InstanceActivation(name)
    listening.listen(lambda: asked_for.append("present"))

    try:
        for _ in range(3):
            InstanceActivation(name).ask_the_running_window(
                timeout_ms=_ANSWER_TIMEOUT_MS
            )
        qtbot.waitUntil(lambda: len(asked_for) == 3, timeout=_ANSWER_TIMEOUT_MS)
    finally:
        listening.close()


def test_a_socket_left_behind_by_a_crash_does_not_block_the_next_launch(
    tmp_path: Path,
) -> None:
    """A process that is killed runs no cleanup, and on Unix its socket file
    stays behind. Taking the installation lock already proved no other process
    is running this installation, so that file is provably a leftover.
    """
    name = activation_name_for(tmp_path / "tiewtrade.sqlite3")
    crashed = QLocalServer()
    assert crashed.listen(name) is True
    # What a kill leaves: the name still taken, nothing tending it. Dropping
    # the C++ object without close() is as near as a test gets to that.
    del crashed

    reopened = InstanceActivation(name)
    try:
        assert reopened.listen(lambda: None) is True
    finally:
        reopened.close()


def test_closing_hands_the_name_on(tmp_path: Path) -> None:
    """Cleanup is not decoration: a name still held is a launch refused."""
    name = activation_name_for(tmp_path / "tiewtrade.sqlite3")
    first = InstanceActivation(name)
    first.listen(lambda: None)
    first.close()

    second = InstanceActivation(name)
    try:
        assert second.listen(lambda: None) is True
    finally:
        second.close()


def test_closing_what_never_listened_does_nothing(tmp_path: Path) -> None:
    InstanceActivation(activation_name_for(tmp_path / "tiewtrade.sqlite3")).close()


def test_closing_twice_does_nothing(tmp_path: Path) -> None:
    activation = InstanceActivation(activation_name_for(tmp_path / "tiewtrade.sqlite3"))
    activation.listen(lambda: None)

    activation.close()
    activation.close()


def test_a_closed_channel_no_longer_answers(tmp_path: Path) -> None:
    """The window is gone by then, so an answer would be a promise nobody keeps."""
    name = activation_name_for(tmp_path / "tiewtrade.sqlite3")
    listening = InstanceActivation(name)
    listening.listen(lambda: None)
    listening.close()

    assert (
        InstanceActivation(name).ask_the_running_window(timeout_ms=_ANSWER_TIMEOUT_MS)
        is False
    )
