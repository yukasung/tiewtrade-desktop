"""Which of the three things a Start is, and whether it asks before doing it."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from tests.support.chart_candles import flat_candle
from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.open_basket import (
    OpenBasketEntry,
    OpenBasketState,
    ReopenedBasket,
    reopen_basket,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.ui.open_position_workflow import OpenPositionWorkflow
from tiewtrade.ui.start_workflow import StartWorkflow
from tiewtrade.ui.take_profit_confirmation import UNREADABLE_POSITION_MESSAGE

# One Entry taken at 100 with an ATR of 2 and a multiplier of 3, so its target
# is 106: a price above that is one a Start would sell at.


def _reopened() -> ReopenedBasket:
    session = configured_spot_session()
    return reopen_basket(
        OpenBasketState(
            basket_id=UUID("00000000-0000-0000-0000-0000000000f1"),
            entries=(
                OpenBasketEntry(
                    entry_number=1,
                    price=Decimal("100"),
                    quantity=Decimal("2"),
                    filled_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
                    atr=Decimal("2"),
                    group_id=1,
                ),
            ),
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        tick_size=Decimal("0.01"),
    )


def _started_and_asked(
    qtbot: QtBot,
    *,
    holding: bool,
    price: int | None,
    session: ConfiguredPaperSession | None = None,
) -> tuple[StartWorkflow, list[str], list[tuple[str, Callable[[], None]]]]:
    """A workflow over a Position, and what it did when Start was pressed."""
    on_screen = configured_spot_session() if session is None else session
    reopened = _reopened() if holding else None
    position = OpenPositionWorkflow(read_open_basket=lambda _session: reopened)
    starts: list[str] = []
    asked: list[tuple[str, Callable[[], None]]] = []
    parent = QWidget()
    qtbot.addWidget(parent)
    workflow = StartWorkflow(
        position=position,
        current_session=lambda: on_screen,
        start_bot=lambda: starts.append("started"),
        dialog_parent=parent,
        confirmation=lambda message, confirm: asked.append((message, confirm)),
    )
    if holding:
        position.read_for(on_screen)
        qtbot.waitUntil(lambda: position.basket is not None)
    if price is not None:
        position.mark_price(flat_candle(datetime(2026, 8, 1, 12, 5, tzinfo=UTC), price))
    return workflow, starts, asked


def test_a_start_with_nothing_held_asks_nothing_and_starts(qtbot: QtBot) -> None:
    workflow, starts, asked = _started_and_asked(qtbot, holding=False, price=105)

    workflow.requested()

    qtbot.waitUntil(lambda: starts == ["started"])
    assert asked == []


def test_a_start_that_would_sell_at_a_target_asks_before_it_does(
    qtbot: QtBot,
) -> None:
    """There is no way back from a Fill, so the sale is named before it happens."""
    workflow, starts, asked = _started_and_asked(qtbot, holding=True, price=110)

    workflow.requested()

    qtbot.waitUntil(lambda: len(asked) == 1)
    assert starts == []
    assert "already at its Take Profit" in asked[0][0]


def test_the_bot_starts_once_the_question_is_answered(qtbot: QtBot) -> None:
    workflow, starts, asked = _started_and_asked(qtbot, holding=True, price=110)
    workflow.requested()
    qtbot.waitUntil(lambda: len(asked) == 1)

    asked[0][1]()

    assert starts == ["started"]


def test_a_start_over_a_position_short_of_its_target_still_asks(
    qtbot: QtBot,
) -> None:
    """It sells nothing, but it is still the press that hands it to a Bot."""
    workflow, starts, asked = _started_and_asked(qtbot, holding=True, price=101)

    workflow.requested()

    qtbot.waitUntil(lambda: len(asked) == 1)
    assert starts == []


def test_a_position_with_no_price_yet_is_asked_about_rather_than_started(
    qtbot: QtBot,
) -> None:
    """Not knowing is not the same as knowing nothing is at its target.

    A window that had simply not been handed a price yet once started on a
    Position it could say nothing about.
    """
    workflow, starts, asked = _started_and_asked(qtbot, holding=True, price=None)

    workflow.requested()

    qtbot.waitUntil(lambda: len(asked) == 1)
    assert asked[0][0] == UNREADABLE_POSITION_MESSAGE
    assert starts == []


def test_a_second_press_while_the_question_stands_does_not_ask_twice(
    qtbot: QtBot,
) -> None:
    workflow, _starts, asked = _started_and_asked(qtbot, holding=True, price=110)
    workflow.requested()
    qtbot.waitUntil(lambda: len(asked) == 1)

    workflow.requested()

    assert len(asked) == 1


def test_a_start_with_no_session_to_start_asks_nothing_and_starts(
    qtbot: QtBot,
) -> None:
    """No Session to read for is nothing to wait on, and nothing to ask about."""
    on_screen: ConfiguredPaperSession | None = None
    position = OpenPositionWorkflow(read_open_basket=lambda _session: None)
    starts: list[str] = []
    parent = QWidget()
    qtbot.addWidget(parent)
    workflow = StartWorkflow(
        position=position,
        current_session=lambda: on_screen,
        start_bot=lambda: starts.append("started"),
        dialog_parent=parent,
        confirmation=lambda _message, _confirm: None,
    )

    workflow.requested()

    assert starts == ["started"]
