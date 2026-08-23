from pytestqt.qtbot import QtBot

from tiewtrade.ui.connection_notice import CONNECTION_LOST_TEXT, ConnectionNoticeCard


def test_the_card_stays_out_of_layout_until_there_is_something_to_say(
    qtbot: QtBot,
) -> None:
    """A card with nothing to say should not hold open space for it."""
    card = ConnectionNoticeCard()
    qtbot.addWidget(card)

    assert not card.isVisible()


def test_showing_the_card_says_which_connection_is_away(qtbot: QtBot) -> None:
    card = ConnectionNoticeCard()
    qtbot.addWidget(card)

    card.show()

    assert card.isVisible()
    assert card.label.text() == CONNECTION_LOST_TEXT


def test_the_card_takes_itself_back(qtbot: QtBot) -> None:
    card = ConnectionNoticeCard()
    qtbot.addWidget(card)
    card.show()

    card.hide()

    assert not card.isVisible()
