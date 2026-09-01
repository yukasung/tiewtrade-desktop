from dataclasses import replace
from decimal import Decimal

from tests.support.workspace_snapshots import basket_entries, basket_snapshot
from tiewtrade.application.total_pnl import total_pnl
from tiewtrade.application.trading_workspace import BasketSnapshot


def unmarked_basket_snapshot() -> BasketSnapshot:
    """A Basket read out of the database before any price has marked it."""
    entries = tuple(
        replace(entry, unrealized_pnl=None)
        for entry in basket_entries("63000", "65000")
    )
    return basket_snapshot(
        entries=entries,
        current_price=None,
        unrealized_pnl=None,
    )


def test_a_total_is_unknown_until_the_realized_half_has_been_read() -> None:
    assert total_pnl(realized=None, open_basket=None) is None


def test_nothing_open_leaves_the_realized_total_as_the_whole_answer() -> None:
    assert total_pnl(realized=Decimal("10.5"), open_basket=None) == Decimal("10.5")


def test_an_open_basket_adds_what_it_is_worth_right_now() -> None:
    basket = basket_snapshot(unrealized_pnl=Decimal("1000"))

    assert total_pnl(realized=Decimal("10"), open_basket=basket) == Decimal("1010")


def test_an_open_basket_underwater_subtracts_from_the_total() -> None:
    basket = basket_snapshot(unrealized_pnl=Decimal("-3"))

    assert total_pnl(realized=Decimal("10"), open_basket=basket) == Decimal("7")


def test_an_unmarked_open_basket_makes_the_total_unknown_rather_than_partial() -> None:
    """Realised alone is not the total while a Basket is open and unpriced."""
    basket = unmarked_basket_snapshot()

    assert total_pnl(realized=Decimal("10"), open_basket=basket) is None
