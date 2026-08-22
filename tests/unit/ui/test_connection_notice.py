from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from tiewtrade.ui.connection_notice import (
    CONNECTION_LOST_TEXT,
    ConnectionNoticeDialog,
)


def _open_boxes(parent: QWidget) -> list[QMessageBox]:
    return [box for box in parent.findChildren(QMessageBox) if box.isVisible()]


def test_the_notice_says_which_connection_is_away_and_what_it_costs(
    qtbot: QtBot,
) -> None:
    """The reader is looking at prices that stopped being true."""
    parent = QWidget()
    qtbot.addWidget(parent)
    notice = ConnectionNoticeDialog(parent)

    notice.show()

    boxes = _open_boxes(parent)
    assert len(boxes) == 1
    assert boxes[0].text() == CONNECTION_LOST_TEXT
    # No Entry opens off a candle refetched after the outage, and saying so
    # is the difference between waiting and reaching for the Stop button.
    assert "backfilled" in boxes[0].informativeText().lower()


def test_the_notice_takes_itself_back(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    notice = ConnectionNoticeDialog(parent)
    notice.show()

    notice.hide()

    assert _open_boxes(parent) == []


def test_showing_a_notice_already_up_does_not_stack_a_second_one(
    qtbot: QtBot,
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    notice = ConnectionNoticeDialog(parent)
    notice.show()

    notice.show()

    assert len(_open_boxes(parent)) == 1


def test_hiding_a_notice_that_was_never_shown_is_nothing_at_all(
    qtbot: QtBot,
) -> None:
    """Every path that clears the notice runs whether or not it went up."""
    parent = QWidget()
    qtbot.addWidget(parent)
    notice = ConnectionNoticeDialog(parent)

    notice.hide()
    notice.hide()

    assert _open_boxes(parent) == []


def test_a_notice_the_reader_closed_stays_closed(qtbot: QtBot) -> None:
    """Dismissing it is an answer, not a pause until the next update."""
    parent = QWidget()
    qtbot.addWidget(parent)
    notice = ConnectionNoticeDialog(parent)
    notice.show()
    _open_boxes(parent)[0].done(0)

    notice.hide()

    assert _open_boxes(parent) == []
