import threading
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from PySide6.QtCore import QThreadPool
from pytestqt.qtbot import QtBot

from tests.support.chart_candles import flat_candle
from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.open_basket import (
    OpenBasketEntry,
    OpenBasketState,
    ReopenedBasket,
    reopen_basket,
)
from tiewtrade.application.paper_runtime import PaperPositionRestoreError
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trading_workspace import (
    POSITION_BASKET_UNREADABLE_MESSAGE,
    PositionBasketTabSnapshot,
    WorkspaceTabState,
)
from tiewtrade.ui.open_position_workflow import OpenPositionWorkflow

OBSERVED_AT = datetime(2026, 8, 1, 12, 5, tzinfo=UTC)


def _reopened() -> ReopenedBasket:
    """One Entry taken at 100 with an ATR of 2 and a multiplier of 3.

    Its target is 106, so a price above that is one a Start would sell at.
    """
    session = configured_spot_session()
    return reopen_basket(
        OpenBasketState(
            basket_id=UUID("00000000-0000-0000-0000-0000000000e1"),
            entries=(
                OpenBasketEntry(
                    entry_number=1,
                    price=Decimal("100"),
                    quantity=Decimal("2"),
                    filled_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
                    atr=Decimal("2"),
                ),
            ),
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        stop_loss_atr_multiplier=None,
        tick_size=Decimal("0.01"),
    )


def _workflow(
    read_open_basket: object = None,
) -> tuple[OpenPositionWorkflow, list[PositionBasketTabSnapshot]]:
    workflow = OpenPositionWorkflow(
        read_open_basket=read_open_basket,  # type: ignore[arg-type]
        thread_pool=QThreadPool(),
        clock=lambda: OBSERVED_AT,
    )
    shown: list[PositionBasketTabSnapshot] = []
    workflow.snapshot_changed.connect(shown.append)
    return workflow, shown


def test_a_basket_read_back_is_marked_on_the_price_of_the_moment(
    qtbot: QtBot,
) -> None:
    """The Basket outlives the Bot, so something has to mark it meanwhile."""
    workflow, shown = _workflow(lambda _session: _reopened())

    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)
    workflow.mark_price(flat_candle(OBSERVED_AT, 110))

    ready = shown[-1]
    assert ready.state is WorkspaceTabState.READY
    assert ready.basket is not None
    assert ready.basket.entry_count == 1
    # Marked 10 above the price it paid, on a position of 2.
    assert ready.basket.unrealized_pnl == Decimal("20")


def test_a_basket_held_before_any_price_shows_everything_but_the_marking(
    qtbot: QtBot,
) -> None:
    """What was bought is answered by the database, not by the next candle.

    Only the marking waits on a price. Holding the rows back for it left an
    empty table over an open Position, which reads as nothing being held.
    """
    workflow, shown = _workflow(lambda _session: _reopened())

    workflow.read_for(configured_spot_session())

    qtbot.waitUntil(lambda: bool(shown))
    held = shown[-1]
    assert held.state is WorkspaceTabState.READY
    assert held.basket is not None
    assert held.basket.symbol == configured_spot_session().market_data.symbol
    assert held.basket.entry_count == 1
    assert held.basket.entries[0].price == Decimal("100")
    assert held.basket.entries[0].take_profit_price == Decimal("106")
    # The one reading a price answers, and there is no price yet.
    assert held.basket.current_price is None
    assert held.basket.unrealized_pnl is None
    assert held.basket.entries[0].unrealized_pnl is None


def test_a_basket_out_of_reach_says_so_rather_than_nothing(qtbot: QtBot) -> None:
    """ "No open Position" would be the one answer that is not true."""

    def unreadable(_session: ConfiguredPaperSession) -> ReopenedBasket:
        raise PaperPositionRestoreError("open Basket has an Entry without its ATR")

    workflow, shown = _workflow(unreadable)

    workflow.read_for(configured_spot_session())

    qtbot.waitUntil(lambda: bool(shown))
    assert shown[-1].state is WorkspaceTabState.ERROR
    assert shown[-1].message == POSITION_BASKET_UNREADABLE_MESSAGE
    assert workflow.basket is None


def test_a_read_asked_for_while_one_is_in_flight_is_not_dropped(qtbot: QtBot) -> None:
    """The answer in flight is about an older Basket than the one being asked.

    Dropping the second ask leaves nothing held, which reads as no Position at
    all — so it is asked again once the first one lands.
    """
    session = configured_spot_session()
    reads: list[int] = []
    release = threading.Event()

    def read_open_basket(_session: ConfiguredPaperSession) -> ReopenedBasket:
        reads.append(len(reads) + 1)
        if len(reads) == 1:
            release.wait(timeout=5)
        return _reopened()

    workflow, _shown = _workflow(read_open_basket)

    workflow.read_for(session)
    qtbot.waitUntil(lambda: len(reads) == 1)
    workflow.read_for(session)
    assert workflow.basket is None
    release.set()

    qtbot.waitUntil(lambda: len(reads) == 2)
    qtbot.waitUntil(lambda: workflow.basket is not None)


def test_nothing_is_said_about_a_basket_the_runtime_has_taken_over(
    qtbot: QtBot,
) -> None:
    """A running Runtime publishes the Basket itself, every candle.

    Saying it again from here would show the Position as it was read from the
    database rather than as the Bot is trading it.
    """
    workflow, shown = _workflow(lambda _session: _reopened())
    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)
    workflow.mark_price(flat_candle(OBSERVED_AT, 110))
    said = len(shown)

    workflow.held_by_runtime()
    workflow.reassert()

    assert workflow.basket is None
    assert len(shown) == said


def test_a_closed_position_is_forgotten_rather_than_asked_about(
    qtbot: QtBot,
) -> None:
    """What was held has just been sold, so no question is left to ask of it."""
    workflow, _shown = _workflow(lambda _session: _reopened())
    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)
    workflow.mark_price(flat_candle(OBSERVED_AT, 110))
    assert workflow.sells_on_start_message() is not None

    workflow.forget()

    assert workflow.sells_on_start_message() is None


def test_a_price_short_of_every_target_leaves_nothing_to_ask(qtbot: QtBot) -> None:
    workflow, _shown = _workflow(lambda _session: _reopened())
    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)

    workflow.mark_price(flat_candle(OBSERVED_AT, 105))

    assert workflow.sells_on_start_message() is None


def test_the_position_at_its_target_is_described_from_the_same_reading(
    qtbot: QtBot,
) -> None:
    """One Basket and one price answer both questions, or neither of them."""
    workflow, _shown = _workflow(lambda _session: _reopened())
    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)

    workflow.mark_price(flat_candle(OBSERVED_AT, 110))
    assert workflow.position_at_target_message() is not None

    workflow.mark_price(flat_candle(OBSERVED_AT, 105))
    assert workflow.position_at_target_message() is None

    workflow.mark_price(flat_candle(OBSERVED_AT, 110))
    workflow.forget()
    assert workflow.position_at_target_message() is None


def test_nothing_read_yet_is_not_read_as_nothing_at_its_target(
    qtbot: QtBot,
) -> None:
    """A Start before the read lands must not pass for a Start with no sale.

    The window asks this before it starts, and answered "no" here it once
    started onto a Position at its target and sold it there.
    """
    workflow, _shown = _workflow(lambda _session: _reopened())
    assert workflow.what_a_start_sells_is_unknown() is False

    workflow.read_for(configured_spot_session())

    assert workflow.what_a_start_sells_is_unknown() is True
    qtbot.waitUntil(lambda: workflow.basket is not None)
    # Held, but with no price to judge the targets by, which is still no
    # answer about what a Start would sell.
    assert workflow.sells_on_start_message() is None
    assert workflow.what_a_start_sells_is_unknown() is True

    workflow.mark_price(flat_candle(OBSERVED_AT, 105))

    assert workflow.what_a_start_sells_is_unknown() is False


def test_a_basket_out_of_reach_leaves_the_sale_unknown(qtbot: QtBot) -> None:
    """A read that failed knows less than one that came back empty."""

    def unreadable(_session: ConfiguredPaperSession) -> ReopenedBasket:
        raise PaperPositionRestoreError("open Basket has an Entry without its ATR")

    workflow, shown = _workflow(unreadable)

    workflow.read_for(configured_spot_session())

    qtbot.waitUntil(lambda: bool(shown))
    assert workflow.what_a_start_sells_is_unknown() is True


def test_a_session_holding_nothing_puts_no_question(qtbot: QtBot) -> None:
    """Read back empty is an answer, so a Start needs no confirming."""
    workflow, _shown = _workflow(lambda _session: None)

    workflow.read_for(configured_spot_session())

    qtbot.waitUntil(lambda: workflow.what_a_start_sells_is_unknown() is False)
    assert workflow.sells_on_start_message() is None


def test_what_is_held_is_answered_without_waiting_for_a_price(
    qtbot: QtBot,
) -> None:
    """A count needs no price, unlike what a Start would sell at.

    Only the Basket decides this one, so it answers as soon as the read lands
    and falls silent the moment what it described is gone.
    """
    workflow, _shown = _workflow(lambda _session: _reopened())
    assert workflow.position_held_message() is None

    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)

    assert workflow.position_held_message() == "1 Entry is open."
    # No price has arrived, and the two that need one still say nothing.
    assert workflow.sells_on_start_message() is None
    assert workflow.position_at_target_message() is None

    workflow.forget()

    assert workflow.position_held_message() is None


def test_a_start_that_would_take_the_whole_basket_leaves_nothing_holding(
    qtbot: QtBot,
) -> None:
    """Read from the same Basket and price as what a Start would sell.

    Nothing to read it from answers as a Basket taken whole, which is the
    answer to be wrong towards: it is the question that offers no Start.
    """
    workflow, _shown = _workflow(lambda _session: _reopened())
    assert workflow.leaves_entries_holding() is False

    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)
    # Held, but with no price to judge the target by.
    assert workflow.leaves_entries_holding() is False

    workflow.mark_price(flat_candle(OBSERVED_AT, 105))
    assert workflow.leaves_entries_holding() is True

    workflow.mark_price(flat_candle(OBSERVED_AT, 110))
    assert workflow.leaves_entries_holding() is False


def test_a_read_that_comes_back_empty_clears_the_tab(qtbot: QtBot) -> None:
    """The database has just been asked, and answered: nothing is held.

    Reasserting cannot say that — it exists to keep a held Basket over a
    snapshot claiming there is none — so the last Entry a trader sold sat on
    the tab as though it were still theirs.
    """
    held: list[ReopenedBasket | None] = [_reopened()]
    workflow, shown = _workflow(lambda _session: held[0])
    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)
    workflow.mark_price(flat_candle(OBSERVED_AT, 105))
    qtbot.waitUntil(lambda: shown[-1].state is WorkspaceTabState.READY)

    held[0] = None
    workflow.read_for(configured_spot_session())

    qtbot.waitUntil(lambda: shown[-1].state is WorkspaceTabState.EMPTY)
    assert shown[-1].basket is None


def test_a_basket_that_has_just_been_closed_leaves_the_tab(qtbot: QtBot) -> None:
    """Dropping it quietly left it on screen after it was sold."""
    workflow, shown = _workflow(lambda _session: _reopened())
    workflow.read_for(configured_spot_session())
    qtbot.waitUntil(lambda: workflow.basket is not None)
    workflow.mark_price(flat_candle(OBSERVED_AT, 105))
    qtbot.waitUntil(lambda: shown[-1].state is WorkspaceTabState.READY)

    workflow.forget()

    assert shown[-1].state is WorkspaceTabState.EMPTY
    assert shown[-1].basket is None
