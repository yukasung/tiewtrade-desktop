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
    position_at_target_message,
    position_held_message,
    sells_on_start_message,
    sweeps_on_start,
)

FILLED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _reopened(*prices: str) -> ReopenedBasket:
    """A Basket of Entries at the given prices, sharing one target.

    The multiplier and ATR put the target 6 above what the Basket paid on
    average, so a price picked in a test says plainly whether it reaches it.
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


def test_a_price_short_of_the_target_has_nothing_to_confirm() -> None:
    """Starting sells nothing, so there is no question to put."""
    assert sells_on_start_message(_reopened("100"), Decimal("105.99")) is None


def test_a_position_at_its_target_is_warned_about_once_however_many_entries() -> None:
    """The sale books at the target, and that is the whole warning.

    Counting Entries grew the box by a clause each, for figures the Position
    tab already shows in columns behind it — and under one Basket target the
    count says nothing about the sale anyway: it takes everything.
    """
    expected = (
        "The Position is already at its Take Profit. Starting the Bot sells "
        "every Entry at once at that target, not at the price now."
    )

    assert sells_on_start_message(_reopened("100"), Decimal("110")) == expected
    assert sells_on_start_message(_reopened("100", "101"), Decimal("110")) == expected


def test_an_entry_under_water_is_swept_out_with_the_rest() -> None:
    """The Entry paid 120 for is losing at 110 and leaves anyway.

    Its average with the Entry at 100 is 110, so the Basket's target is 116 —
    which is the point of measuring from the average: the Entry that would
    otherwise be stranded goes when the Basket goes.
    """
    reopened = _reopened("100", "120")

    assert reopened.basket.take_profit_price == Decimal("116")
    assert sweeps_on_start(reopened, Decimal("116")) is True
    assert sweeps_on_start(reopened, Decimal("115.99")) is False


def test_no_price_is_quoted_where_none_can_be_formatted_well() -> None:
    """A price from the chart carries the exchange's full precision.

    64,134.21000000 is what that reads as in prose. The tab formats it; this
    message no longer has to.
    """
    message = sells_on_start_message(_reopened("100"), Decimal("110.21000000"))

    assert message is not None
    assert "110" not in message
    assert "106" not in message


def test_a_price_short_of_the_target_leaves_the_position_nothing_to_say() -> None:
    """Both messages read the same Basket, so both fall silent together."""
    assert position_at_target_message(_reopened("100"), Decimal("105.99")) is None


def test_the_position_at_its_target_is_stated_without_a_sale_it_cannot_make() -> None:
    """The dialog this feeds offers no Start, so it promises no target sale."""
    expected = "The Position is already at its Take Profit."

    assert position_at_target_message(_reopened("100"), Decimal("110")) == expected
    assert (
        position_at_target_message(_reopened("100", "101"), Decimal("110")) == expected
    )


def test_a_position_reads_as_how_much_is_open_and_nothing_more() -> None:
    """Resuming trades nothing, so there is nothing to warn about.

    What the Position is worth is a row in the tab behind the dialog, not a
    clause that grows one longer with every Entry.
    """
    assert position_held_message(_reopened("100")) == "1 Entry is open."
    assert position_held_message(_reopened("100", "120")) == "2 Entries are open."
    # Neither what they were paid nor what they are worth: both are columns.
    assert "120" not in position_held_message(_reopened("100", "120"))
