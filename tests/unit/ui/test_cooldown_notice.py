from datetime import UTC, datetime

from pytestqt.qtbot import QtBot

from tests.support.workspace_snapshots import basket_snapshot
from tiewtrade.application.trading_workspace import BasketEntryGate
from tiewtrade.ui.cooldown_notice import CooldownNoticeCard


def test_the_card_says_nothing_while_the_gate_is_open(qtbot: QtBot) -> None:
    card = CooldownNoticeCard()
    qtbot.addWidget(card)
    card.show()

    card.show_basket(basket_snapshot(entry_gate=BasketEntryGate.OPEN))

    assert not card.isVisible()


def test_a_cooldown_names_the_day_it_lifts_on(qtbot: QtBot) -> None:
    card = CooldownNoticeCard()
    qtbot.addWidget(card)
    card.show()

    card.show_basket(
        basket_snapshot(
            entry_gate=BasketEntryGate.COOLDOWN,
            cooldown_ends_at_utc=datetime(2026, 8, 31, 12, tzinfo=UTC),
        )
    )

    assert card.isVisible()
    # The same phrase the Position tab reads, so one Cooldown is not read as
    # two different waits on one screen.
    assert card.label.text().startswith("Cooldown until 2026-08-31")


def test_a_cooldown_with_no_day_still_says_the_gate_is_shut(qtbot: QtBot) -> None:
    card = CooldownNoticeCard()
    qtbot.addWidget(card)
    card.show()

    card.show_basket(basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN))

    assert card.isVisible()
    assert card.label.text().startswith("Cooldown")


def test_the_card_takes_itself_back_once_the_cooldown_lifts(qtbot: QtBot) -> None:
    card = CooldownNoticeCard()
    qtbot.addWidget(card)
    card.show()
    card.show_basket(basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN))

    card.show_basket(basket_snapshot(entry_gate=BasketEntryGate.OPEN))

    assert not card.isVisible()


def test_no_open_basket_says_nothing(qtbot: QtBot) -> None:
    card = CooldownNoticeCard()
    qtbot.addWidget(card)
    card.show()
    card.show_basket(basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN))

    card.show_basket(None)

    assert not card.isVisible()
