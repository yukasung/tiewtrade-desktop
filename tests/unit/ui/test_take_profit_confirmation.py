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
from tiewtrade.ui.take_profit_confirmation import sells_on_start_message

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
        "once at 106, the target it was given, not at the current 110."
    )


def test_every_entry_the_price_reached_is_named_by_the_target_it_books_at() -> None:
    """The targets are the prices the sale makes, which is why they are named.

    They are what a Start books, and they are not the price on screen — the
    whole reason the question is worth asking.
    """
    message = sells_on_start_message(_reopened("100", "101"), Decimal("110"))

    assert message == (
        "2 Entries are already at their Take Profit. Starting the Bot sells "
        "them at once at 106 and 107, the targets they were given, "
        "not at the current 110."
    )


def test_an_entry_left_short_is_not_counted_among_the_ones_sold() -> None:
    """A Start sells what the price reached and leaves the rest holding."""
    message = sells_on_start_message(_reopened("100", "120"), Decimal("110"))

    assert message is not None
    assert message.startswith("1 Entry is already at its Take Profit.")
    assert "106" in message
    assert "126" not in message
