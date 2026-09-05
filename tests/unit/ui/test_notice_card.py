from pytestqt.qtbot import QtBot

from tiewtrade.ui.notice_card import NoticeCard, NoticeLevel


def test_a_card_with_nothing_to_say_holds_no_room_open(qtbot: QtBot) -> None:
    card = NoticeCard(NoticeLevel.INFO)
    qtbot.addWidget(card)

    assert not card.isVisible()
    assert card.label.text() == ""


def test_saying_something_puts_the_card_up_with_the_words_on_it(qtbot: QtBot) -> None:
    card = NoticeCard(NoticeLevel.WARNING)
    qtbot.addWidget(card)

    card.say("Reconnecting")

    assert card.isVisible()
    assert card.label.text() == "Reconnecting"


def test_saying_nothing_takes_the_card_back(qtbot: QtBot) -> None:
    card = NoticeCard(NoticeLevel.WARNING)
    qtbot.addWidget(card)
    card.say("Reconnecting")

    card.say("")

    assert not card.isVisible()


def test_the_level_is_readable_by_the_stylesheet(qtbot: QtBot) -> None:
    """Two notices sitting in one column are told apart by colour alone."""
    warning = NoticeCard(NoticeLevel.WARNING)
    info = NoticeCard(NoticeLevel.INFO)
    qtbot.addWidget(warning)
    qtbot.addWidget(info)

    assert warning.objectName() == info.objectName() == "noticeCard"
    assert warning.property("level") == "warning"
    assert info.property("level") == "info"
