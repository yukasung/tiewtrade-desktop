from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.trading.basket import (
    Basket,
    BasketCloseReason,
    SettledEntries,
    average_entry_price_after_fill,
)
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.position import PositionSide


def policy(max_entries: int = 10) -> EntryPolicy:
    return EntryPolicy(max_entries=max_entries)


def basket_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000092")


def test_take_profit_price_is_read_only() -> None:
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
    )

    with pytest.raises(AttributeError):
        setattr(basket, "take_profit_price", Decimal("999"))  # noqa: B010

    assert basket.take_profit_price is None


def test_the_basket_carries_one_target_measured_from_its_average() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("3"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2.03"),
        tick_size=Decimal("0.1"),
    )
    # One Entry, so its price is the average: 100 + 3 x 2.03, floored to tick.
    assert basket.take_profit_price == Decimal("106.0")

    basket.add_entry(
        price=Decimal("90"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 2, tzinfo=UTC),
        atr=Decimal("3.03"),
        tick_size=Decimal("0.1"),
    )

    assert basket.average_entry_price == Decimal("97.5")
    # 97.5 + 3 x 3.03, floored to the tick. One target for the whole Basket,
    # measured from what it paid on average and from the ATR of its latest
    # Fill — the Entry bought at 100 leaves at this price too.
    assert basket.take_profit_price == Decimal("106.5")
    assert [entry.entry_number for entry in basket.entries] == [1, 2]


def test_each_entry_is_measured_from_what_that_entry_paid() -> None:
    """An Entry is the unit that leaves, so an Entry is the unit that is measured.

    Three Entries, three targets, each one the price its own Fill paid plus
    the ATR that Fill was read with — and one more of the Basket's own,
    further down, that would take whatever is left together.
    """
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="90", atr="2")
    _fill(basket, 3, price="80", atr="2")

    assert basket.entry_take_profit_price(1) == Decimal("106.0")
    assert basket.entry_take_profit_price(2) == Decimal("96.0")
    assert basket.entry_take_profit_price(3) == Decimal("86.0")
    # The whole Basket paid 90, which sits between the outermost targets.
    assert basket.average_entry_price == Decimal("90")
    assert basket.take_profit_price == Decimal("96.0")


def test_an_entry_is_measured_against_the_atr_of_its_own_fill() -> None:
    """Two Entries at one price part company when their ATR differs."""
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="100", atr="4")

    assert basket.entry_take_profit_price(1) == Decimal("106.0")
    assert basket.entry_take_profit_price(2) == Decimal("112.0")


def test_an_entry_that_reaches_its_own_target_leaves_at_that_target() -> None:
    """The cheapest Entry goes first, at the price it was aiming at."""
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="90", atr="2")
    _fill(basket, 3, price="80", atr="2")

    exits = basket.take_profit_exits(Decimal("86.0"), Decimal("80"))

    assert [(exit.entry.entry_number, exit.price) for exit in exits] == [
        (3, Decimal("86.0"))
    ]


def test_a_candle_reaching_the_basket_target_sweeps_the_entry_left_behind() -> None:
    """The net: the Entry still under water leaves with the ones in profit.

    Entries 2 and 3 reached their own targets and sell there, because a limit
    order at 86 or at 96 would have filled before the price came any higher.
    Entry 1 never reached 106 — it is swept at the Basket's 96, which is below
    its own target and above what it paid, which is the trade the net exists
    to make.
    """
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="90", atr="2")
    _fill(basket, 3, price="80", atr="2")

    exits = basket.take_profit_exits(Decimal("96.0"), Decimal("80"))

    assert [(exit.entry.entry_number, exit.price) for exit in exits] == [
        (3, Decimal("86.0")),
        (2, Decimal("96.0")),
        (1, Decimal("96.0")),
    ]


def test_an_entry_target_stands_still_while_the_entries_around_it_change() -> None:
    """Nothing another Entry does moves the price this one is waiting for."""
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="90", atr="2")
    assert basket.entry_take_profit_price(1) == Decimal("106.0")

    basket.close_entry(
        2, exit_price=Decimal("95"), closed_at=datetime(2026, 1, 9, tzinfo=UTC)
    )

    assert basket.entry_take_profit_price(1) == Decimal("106.0")
    assert basket.entry_take_profit_price(2) is None


def test_an_entry_the_basket_does_not_hold_has_no_target_to_state() -> None:
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")

    assert basket.entry_take_profit_price(2) is None


def test_every_open_entry_is_shown_with_what_it_paid_and_leaves_at() -> None:
    basket = _long_basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="90", atr="2")

    assert [
        (view.entry_number, view.entry_price, view.take_profit_price)
        for view in basket.open_entry_targets()
    ] == [
        (1, Decimal("100"), Decimal("106.0")),
        (2, Decimal("90"), Decimal("96.0")),
    ]


def test_a_short_entry_is_measured_below_what_it_paid() -> None:
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=PositionSide.SHORT,
    )
    _fill(basket, 1, price="100", atr="2", side=PositionSide.SHORT)
    _fill(basket, 2, price="110", atr="2", side=PositionSide.SHORT)

    # Sold at 100, so it buys back 6 lower.
    assert basket.entry_take_profit_price(1) == Decimal("94.0")
    assert basket.entry_take_profit_price(2) == Decimal("104.0")

    exits = basket.take_profit_exits(Decimal("110"), Decimal("94.0"))

    assert [exit.entry.entry_number for exit in exits] == [2, 1]


def _long_basket() -> Basket:
    return Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )


def _fill(
    basket: Basket,
    number: int,
    *,
    price: str,
    atr: str,
    side: PositionSide = PositionSide.LONG,
) -> None:
    basket.add_entry(
        position_side=side,
        price=Decimal(price),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, number, tzinfo=UTC),
        atr=Decimal(atr),
        tick_size=Decimal("0.1"),
    )


def test_the_target_is_remeasured_when_an_entry_is_sold_out_of_the_basket() -> None:
    """Selling an Entry moves the average, so it has to move the target.

    A target left where a departed Entry put it is measured against a Basket
    that no longer exists.
    """
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("3"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )
    basket.add_entry(
        price=Decimal("90"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 2, tzinfo=UTC),
        atr=Decimal("4"),
        tick_size=Decimal("0.1"),
    )
    # 97.5 + 3 x 4, on the latest Fill's ATR.
    assert basket.take_profit_price == Decimal("109.5")

    basket.close_entry(
        2,
        exit_price=Decimal("95"),
        closed_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    # Only the Entry bought at 100 is left, and the ATR falls back to the one
    # it was itself read against.
    assert basket.average_entry_price == Decimal("100")
    assert basket.take_profit_price == Decimal("106.0")


def test_a_short_basket_takes_profit_below_its_average_and_rounds_up() -> None:
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=PositionSide.SHORT,
    )

    basket.add_entry(
        position_side=PositionSide.SHORT,
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1.03"),
        tick_size=Decimal("0.1"),
    )

    assert basket.position_side is PositionSide.SHORT
    assert basket.take_profit_price == Decimal("97.0")

    basket.add_entry(
        position_side=PositionSide.SHORT,
        price=Decimal("90"),
        quantity=Decimal("3"),
        filled_at=datetime(2026, 1, 2, tzinfo=UTC),
        atr=Decimal("1.03"),
        tick_size=Decimal("0.1"),
    )

    assert basket.average_entry_price == Decimal("92.5")
    # 92.5 - 3 x 1.03 = 89.41, rounded up against the seller.
    assert basket.take_profit_price == Decimal("89.5")


@pytest.mark.parametrize(
    ("side", "high", "low", "expected"),
    [
        (PositionSide.LONG, Decimal("106.0"), Decimal("99"), True),
        (PositionSide.LONG, Decimal("105.9"), Decimal("99"), False),
        (PositionSide.SHORT, Decimal("101"), Decimal("94.0"), True),
        (PositionSide.SHORT, Decimal("101"), Decimal("94.1"), False),
    ],
)
def test_reaching_the_target_is_read_off_the_basket_not_off_the_candle(
    side: PositionSide, high: Decimal, low: Decimal, expected: bool
) -> None:
    """One rule, so an executor and a confirmation cannot answer differently."""
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=side,
    )
    basket.add_entry(
        position_side=side,
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    assert basket.reaches_take_profit(high, low) is expected


def test_an_empty_basket_reaches_no_target() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )

    assert basket.reaches_take_profit(Decimal("999"), Decimal("1")) is False


def test_closing_one_entry_leaves_the_others_where_they_are() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )
    basket.add_entry(
        price=Decimal("90"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 2, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    closed = basket.close_entry(
        2,
        exit_price=Decimal("96"),
        closed_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    assert closed.entry_number == 2
    assert closed.quantity == Decimal("1")
    assert closed.realized_pnl == Decimal("6")
    assert closed.realized_pnl == Decimal("6")
    # The Basket lives on with the Entry that did not sell.
    assert basket.is_closed is False
    assert basket.entry_count == 1
    assert basket.total_quantity == Decimal("2")
    assert basket.average_entry_price == Decimal("100")
    assert [entry.entry_number for entry in basket.entries] == [1]
    # The number the sold Entry used is not handed to the next one.
    assert basket.entry_sequence == 2


def test_selling_the_last_entry_closes_the_basket_on_every_entry_it_held() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    for index, price in enumerate((Decimal("100"), Decimal("90"))):
        basket.add_entry(
            price=price,
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 1 + index, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )
    basket.close_entry(
        2,
        exit_price=Decimal("96"),
        closed_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    basket.close_entry(
        1,
        exit_price=Decimal("106"),
        closed_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    result = basket.settled_result(
        exit_price=Decimal("106"),
        closed_at=datetime(2026, 1, 4, tzinfo=UTC),
    )

    assert basket.is_closed is True
    assert basket.is_empty is True
    # Both Entries are in the Basket's own record, whichever sold first.
    assert result.entry_count == 2
    assert result.average_entry_price == Decimal("95")
    assert result.realized_pnl == Decimal("12")
    assert result.realized_pnl == Decimal("12")


def test_a_basket_still_holding_an_entry_has_no_settled_result() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    with pytest.raises(ValueError, match="still holds an Entry"):
        basket.settled_result(
            exit_price=Decimal("106"),
            closed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_closing_an_entry_the_basket_does_not_hold_is_refused() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    with pytest.raises(ValueError, match="no open Entry with that number"):
        basket.close_entry(
            7,
            exit_price=Decimal("106"),
            closed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_a_manual_close_after_a_sale_reports_the_whole_basket() -> None:
    """The Entries that already sold are part of the Basket's result too."""
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
    )
    for index, price in enumerate((Decimal("100"), Decimal("90"))):
        basket.add_entry(
            price=price,
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 1 + index, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )
    basket.close_entry(
        2,
        exit_price=Decimal("96"),
        closed_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    closed = basket.close(
        exit_price=Decimal("80"),
        closed_at=datetime(2026, 1, 4, tzinfo=UTC),
        close_reason=BasketCloseReason.MANUAL,
    )

    assert closed.entry_count == 2
    assert closed.average_entry_price == Decimal("95")
    # The sale made 6, the manual close lost 20.
    assert closed.realized_pnl == Decimal("-14")
    assert closed.realized_pnl == Decimal("-14")


def test_average_entry_price_after_fill_uses_domain_weighting() -> None:
    assert average_entry_price_after_fill(
        current_average_entry_price=Decimal("100"),
        current_quantity=Decimal("3"),
        fill_price=Decimal("90"),
        fill_quantity=Decimal("1"),
    ) == Decimal("97.5")


def test_one_way_basket_rejects_opposite_side_without_mutation() -> None:
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=PositionSide.LONG,
    )

    with pytest.raises(ValueError, match="opposite"):
        basket.add_entry(
            position_side=PositionSide.SHORT,
            price=Decimal("100"),
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 1, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )

    assert basket.entry_count == 0
    assert basket.take_profit_price is None


def test_invalid_short_take_profit_does_not_mutate_basket() -> None:
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=PositionSide.SHORT,
    )

    with pytest.raises(ValueError, match="take profit price"):
        basket.add_entry(
            position_side=PositionSide.SHORT,
            price=Decimal("2"),
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 1, tzinfo=UTC),
            atr=Decimal("1"),
            tick_size=Decimal("0.1"),
        )

    assert basket.entry_count == 0
    assert basket.take_profit_price is None


def test_basket_requires_positive_take_profit_multiplier() -> None:
    with pytest.raises(ValueError, match="take_profit_atr_multiplier"):
        Basket(
            basket_id=basket_id(),
            policy=policy(),
            take_profit_atr_multiplier=Decimal("0"),
        )


def test_close_exposes_identity_and_pnl_accounting_components() -> None:
    expected_basket_id = basket_id()
    basket = Basket(
        basket_id=expected_basket_id,
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    closed = basket.close(
        exit_price=Decimal("120"),
        closed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert closed.basket_id == expected_basket_id
    assert closed.realized_pnl == Decimal("20")
    assert closed.realized_pnl == Decimal("20")
    assert closed.realized_pnl == closed.realized_pnl
    assert closed.position_side is PositionSide.LONG
    assert closed.close_reason is BasketCloseReason.TAKE_PROFIT


def test_short_basket_close_calculates_directional_pnl() -> None:
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=PositionSide.SHORT,
    )
    basket.add_entry(
        position_side=PositionSide.SHORT,
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    closed = basket.close(
        exit_price=Decimal("90"),
        closed_at=datetime(2026, 1, 2, tzinfo=UTC),
        close_reason=BasketCloseReason.TAKE_PROFIT,
    )

    assert closed.position_side is PositionSide.SHORT
    assert closed.close_reason is BasketCloseReason.TAKE_PROFIT
    assert closed.realized_pnl == Decimal("20")
    assert closed.realized_pnl == Decimal("20")


def test_basket_rejects_entries_beyond_configured_maximum() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(4), take_profit_atr_multiplier=Decimal("3")
    )
    for day in range(1, 5):
        basket.add_entry(
            price=Decimal("100"),
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, day, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )

    with pytest.raises(ValueError, match="maximum entries"):
        basket.add_entry(
            price=Decimal("100"),
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 5, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )


def test_basket_requires_utc_fill_timestamp() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )

    with pytest.raises(ValueError, match="UTC"):
        basket.add_entry(
            price=Decimal("100"),
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 1),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )


def test_basket_requires_utc_close_timestamp() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )

    with pytest.raises(ValueError, match="UTC"):
        basket.close(
            exit_price=Decimal("106"),
            closed_at=datetime(2026, 1, 2),
        )


@pytest.mark.parametrize(
    ("field", "price", "quantity", "fee", "atr", "tick_size"),
    [
        (
            "price",
            Decimal("0"),
            Decimal("1"),
            Decimal("0.1"),
            Decimal("2"),
            Decimal("0.1"),
        ),
        (
            "quantity",
            Decimal("100"),
            Decimal("0"),
            Decimal("0.1"),
            Decimal("2"),
            Decimal("0.1"),
        ),
        (
            "atr",
            Decimal("100"),
            Decimal("1"),
            Decimal("0.1"),
            Decimal("-1"),
            Decimal("0.1"),
        ),
        (
            "tick_size",
            Decimal("100"),
            Decimal("1"),
            Decimal("0.1"),
            Decimal("2"),
            Decimal("0"),
        ),
    ],
)
def test_invalid_entry_does_not_mutate_basket(
    field: str,
    price: Decimal,
    quantity: Decimal,
    fee: Decimal,
    atr: Decimal,
    tick_size: Decimal,
) -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )

    with pytest.raises(ValueError, match=field):
        basket.add_entry(
            price=price,
            quantity=quantity,
            filled_at=datetime(2026, 1, 1, tzinfo=UTC),
            atr=atr,
            tick_size=tick_size,
        )

    assert basket.entry_count == 0


@pytest.mark.parametrize(
    ("field", "price", "quantity", "fee", "atr", "tick_size"),
    [
        (
            "price",
            invalid,
            Decimal("1"),
            Decimal("0.1"),
            Decimal("2"),
            Decimal("0.1"),
        )
        for invalid in (Decimal("NaN"), Decimal("Infinity"))
    ]
    + [
        (
            "quantity",
            Decimal("100"),
            invalid,
            Decimal("0.1"),
            Decimal("2"),
            Decimal("0.1"),
        )
        for invalid in (Decimal("NaN"), Decimal("Infinity"))
    ]
    + [
        (
            "atr",
            Decimal("100"),
            Decimal("1"),
            Decimal("0.1"),
            invalid,
            Decimal("0.1"),
        )
        for invalid in (Decimal("NaN"), Decimal("Infinity"))
    ]
    + [
        (
            "tick_size",
            Decimal("100"),
            Decimal("1"),
            Decimal("0.1"),
            Decimal("2"),
            invalid,
        )
        for invalid in (Decimal("NaN"), Decimal("Infinity"))
    ],
)
def test_non_finite_entry_does_not_mutate_basket(
    field: str,
    price: Decimal,
    quantity: Decimal,
    fee: Decimal,
    atr: Decimal,
    tick_size: Decimal,
) -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )

    with pytest.raises(ValueError, match=field):
        basket.add_entry(
            price=price,
            quantity=quantity,
            filled_at=datetime(2026, 1, 1, tzinfo=UTC),
            atr=atr,
            tick_size=tick_size,
        )

    assert basket.entry_count == 0
    assert basket.take_profit_price is None


def test_closed_basket_cannot_close_or_accept_entries_twice() -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )
    basket.close(
        exit_price=Decimal("106"),
        closed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert basket.is_closed
    with pytest.raises(ValueError, match="closed"):
        basket.close(
            exit_price=Decimal("106"),
            closed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="closed"):
        basket.add_entry(
            price=Decimal("100"),
            quantity=Decimal("1"),
            filled_at=datetime(2026, 1, 3, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )


@pytest.mark.parametrize(
    ("field", "exit_price", "exit_fee"),
    [
        ("exit_price", Decimal("0"), Decimal("0.106")),
    ],
)
def test_invalid_close_does_not_close_basket(
    field: str,
    exit_price: Decimal,
    exit_fee: Decimal,
) -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )
    with pytest.raises(ValueError, match=field):
        basket.close(
            exit_price=exit_price,
            closed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

    assert not basket.is_closed


@pytest.mark.parametrize(
    ("field", "exit_price", "exit_fee"),
    [
        ("exit_price", Decimal("NaN"), Decimal("0.106")),
        ("exit_price", Decimal("Infinity"), Decimal("0.106")),
    ],
)
def test_non_finite_close_does_not_close_basket(
    field: str,
    exit_price: Decimal,
    exit_fee: Decimal,
) -> None:
    basket = Basket(
        basket_id=basket_id(), policy=policy(), take_profit_atr_multiplier=Decimal("3")
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
    )
    with pytest.raises(ValueError, match=field):
        basket.close(
            exit_price=exit_price,
            closed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

    assert not basket.is_closed


def test_a_basket_told_what_it_settled_closes_on_that_too() -> None:
    """A Basket is one record however many times it was sold out of.

    Taken back from the database it cannot replay the Entries that already
    went — they are not in the position any more — so what they left is handed
    to it. Starting that at nothing wrote a result and a count that left out
    every Entry sold before the Runtime restarted.
    """
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        settled=SettledEntries(
            count=1,
            quantity=Decimal("2"),
            notional=Decimal("200"),
            realized_pnl=Decimal("11.34"),
        ),
    )
    basket.add_entry(
        price=Decimal("110"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.01"),
    )

    closed = basket.close(
        exit_price=Decimal("120"),
        closed_at=datetime(2026, 8, 3, tzinfo=UTC),
        close_reason=BasketCloseReason.MANUAL,
    )

    # The Entry it holds made 20; the one it was told about made 11.34.
    assert closed.realized_pnl == Decimal("31.34")
    assert closed.entry_count == 2
    # Averaged over both, at the prices each was paid.
    assert closed.average_entry_price == Decimal("105")


def test_what_a_basket_was_told_it_settled_is_its_own() -> None:
    """The tally handed in is a reading, not a thing the Basket goes on using."""
    settled = SettledEntries(
        count=1,
        quantity=Decimal("2"),
        notional=Decimal("200"),
        realized_pnl=Decimal("10"),
    )
    basket = Basket(
        basket_id=basket_id(),
        policy=policy(),
        take_profit_atr_multiplier=Decimal("3"),
        settled=settled,
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.01"),
    )

    basket.close_entry(
        1,
        exit_price=Decimal("110"),
        closed_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    assert settled.count == 1
    assert settled.realized_pnl == Decimal("10")


def test_a_settled_tally_of_nothing_carries_no_figures() -> None:
    with pytest.raises(ValueError, match="no settled Entry leaves figures"):
        SettledEntries(realized_pnl=Decimal("10"))
    with pytest.raises(ValueError, match="count must not be negative"):
        SettledEntries(count=-1)
