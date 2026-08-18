from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.open_basket import (
    OpenBasketEntry,
    OpenBasketState,
    ReopenedBasket,
    reopen_basket,
)
from tiewtrade.ui.take_profit_confirmation import (
    leaves_entries_holding,
    position_at_target_message,
    position_held_message,
    sells_on_start_message,
)

FILLED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _reopened(*prices: str) -> ReopenedBasket:
    """A Basket of Entries at the given prices, each with its own target.

    The multiplier and ATR put every target 6 above the price it was paid at,
    so a price picked in a test says plainly which Entries it reaches.
    """
    session = configured_spot_session()
    return reopen_basket(
        OpenBasketState(
            basket_id=UUID("00000000-0000-0000-0000-0000000000d1"),
            entries=tuple(
                OpenBasketEntry(
                    entry_number=number,
                    price=Decimal(price),
                    quantity=Decimal("2"),
                    filled_at=FILLED_AT + timedelta(hours=number - 1),
                    atr=Decimal("2"),
                )
                for number, price in enumerate(prices, start=1)
            ),
            closed_basket_count=0,
        ),
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        stop_loss_atr_multiplier=None,
        tick_size=Decimal("0.01"),
    )


def test_a_price_short_of_every_target_has_nothing_to_confirm() -> None:
    """Starting sells nothing, so there is no question to put."""
    assert sells_on_start_message(_reopened("100"), Decimal("105.99")) is None


def test_one_entry_at_its_target_reads_in_the_singular() -> None:
    message = sells_on_start_message(_reopened("100"), Decimal("110"))

    assert message == (
        "1 Entry is already at its Take Profit. Starting the Bot sells it at "
        "once at the target it was given, not at the price now."
    )


def test_the_entries_the_price_reached_are_counted_rather_than_listed() -> None:
    """What is worth saying is that the sale books at targets, not at 110.

    Naming each one grew the box by a clause per Entry, for figures the
    Position tab already shows in columns behind it. The count carries the
    warning; the numbers are read where numbers read well.
    """
    two = sells_on_start_message(_reopened("100", "101"), Decimal("110"))

    assert two == (
        "2 Entries are already at their Take Profit. Starting the Bot sells "
        "them at once at the targets they were given, not at the price now."
    )
    # The targets reached, 106 and 107, named nowhere: the sentence is the
    # same one however many Entries stand behind the count.
    assert "106" not in two
    assert "107" not in two


def test_no_price_is_quoted_where_none_can_be_formatted_well() -> None:
    """A price from the chart carries the exchange's full precision.

    64,134.21000000 is what that reads as in prose. The tab formats it; this
    message no longer has to.
    """
    message = sells_on_start_message(_reopened("100"), Decimal("110.21000000"))

    assert message is not None
    assert "110" not in message
    assert "106" not in message


def test_an_entry_left_short_is_not_counted_among_the_ones_sold() -> None:
    """A Start sells what the price reached and leaves the rest holding."""
    message = sells_on_start_message(_reopened("100", "120"), Decimal("110"))

    assert message is not None
    assert message.startswith("1 Entry is already at its Take Profit.")


def test_a_price_short_of_every_target_leaves_the_position_nothing_to_say() -> None:
    """Both messages read the same Basket, so both fall silent together."""
    assert position_at_target_message(_reopened("100"), Decimal("105.99")) is None


def test_the_position_at_its_target_is_stated_without_a_sale_it_cannot_make() -> None:
    """The dialog this feeds offers no Start, so it promises no target sale."""
    assert position_at_target_message(_reopened("100"), Decimal("110")) == (
        "1 Entry is already at its Take Profit."
    )
    assert position_at_target_message(_reopened("100", "101"), Decimal("110")) == (
        "2 Entries are already at their Take Profit."
    )


def test_an_entry_left_short_is_not_counted_among_the_ones_at_target() -> None:
    message = position_at_target_message(_reopened("100", "120"), Decimal("110"))

    assert message == "1 Entry is already at its Take Profit."


def test_a_position_reads_as_how_much_is_open_and_nothing_more() -> None:
    """Resuming trades nothing, so there is nothing to warn about.

    What the Position is worth is a row in the tab behind the dialog, not a
    clause that grows one longer with every Entry.
    """
    assert position_held_message(_reopened("100")) == "1 Entry is open."
    assert position_held_message(_reopened("100", "120")) == "2 Entries are open."
    # Neither what they were paid nor what they are worth: both are columns.
    assert "120" not in position_held_message(_reopened("100", "120"))


def test_a_basket_a_start_would_take_whole_leaves_nothing_holding() -> None:
    """Every Entry reached: there is nothing a Start would not sell."""
    assert leaves_entries_holding(_reopened("100"), Decimal("110")) is False
    assert leaves_entries_holding(_reopened("100", "101"), Decimal("110")) is False


def test_an_entry_short_of_its_target_is_left_holding() -> None:
    """The Entry at 120 has a target of 126, which 110 has not reached.

    Read from the same rule as the sale, so the two cannot disagree about
    which Entries a Start would take.
    """
    assert leaves_entries_holding(_reopened("100", "120"), Decimal("110")) is True
    # Nothing reached at all is still something left holding.
    assert leaves_entries_holding(_reopened("100"), Decimal("105")) is True
