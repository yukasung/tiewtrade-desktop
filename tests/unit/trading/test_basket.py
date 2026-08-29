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


def test_an_entry_the_basket_does_not_hold_has_no_target_to_state() -> None:
    basket = _basket()
    _fill(basket, 1, price="100", atr="2")

    assert basket.entry_take_profit_price(2) is None


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


def _basket(side: PositionSide = PositionSide.LONG) -> Basket:
    return Basket(
        basket_id=UUID("00000000-0000-0000-0000-000000000092"),
        policy=EntryPolicy(max_entries=10),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=side,
    )


def _fill(
    basket: Basket,
    number: int,
    *,
    price: str,
    atr: str = "2",
    quantity: str = "1",
    side: PositionSide = PositionSide.LONG,
) -> None:
    basket.add_entry(
        position_side=side,
        price=Decimal(price),
        quantity=Decimal(quantity),
        filled_at=datetime(2026, 1, number, tzinfo=UTC),
        atr=Decimal(atr),
        tick_size=Decimal("0.1"),
    )


def _groups(basket: Basket) -> list[int]:
    return [entry.group_id for entry in basket.entries]


def test_an_entry_that_averages_down_joins_the_group_it_averaged() -> None:
    """The group's target is restated from what the group now paid on average."""
    basket = _basket()
    _fill(basket, 1, price="100")
    assert basket.entry_take_profit_price(1) == Decimal("106.0")

    _fill(basket, 2, price="90")

    assert _groups(basket) == [1, 1]
    # The group paid 95 on average, so both Entries leave at 95 + 3 x 2.
    assert basket.entry_take_profit_price(1) == Decimal("101.0")
    assert basket.entry_take_profit_price(2) == Decimal("101.0")


def test_an_entry_that_averages_up_opens_a_group_of_its_own() -> None:
    """The group already open keeps the target it already has."""
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="110")

    assert _groups(basket) == [1, 2]
    assert basket.entry_take_profit_price(1) == Decimal("106.0")
    assert basket.entry_take_profit_price(2) == Decimal("116.0")


def test_an_entry_at_the_group_average_opens_a_group_of_its_own() -> None:
    """Averaging up is anything that does not average down, the equal price too."""
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="100")

    assert _groups(basket) == [1, 2]


def test_averaging_down_is_measured_against_the_latest_group_only() -> None:
    """A price below an older group but above the latest one still opens a group."""
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="120")
    # Below group 1's average, but group 2 is the one a new Entry is measured
    # against, and 110 is below 120.
    _fill(basket, 3, price="110")

    assert _groups(basket) == [1, 2, 2]
    assert basket.entry_take_profit_price(1) == Decimal("106.0")
    assert basket.entry_take_profit_price(2) == Decimal("121.0")
    assert basket.entry_take_profit_price(3) == Decimal("121.0")


def test_a_group_target_moves_closer_but_never_further_away() -> None:
    """A rising ATR must not push a target past the price it already sits at."""
    basket = _basket()
    _fill(basket, 1, price="100", atr="2")
    assert basket.entry_take_profit_price(1) == Decimal("106.0")

    # 99.5 + 3 x 8 = 123.5 on its own, which is further away than 106.
    _fill(basket, 2, price="99", atr="8")

    assert _groups(basket) == [1, 1]
    assert basket.entry_take_profit_price(1) == Decimal("106.0")


def test_a_candle_that_reaches_one_group_leaves_the_others_holding() -> None:
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="90")
    _fill(basket, 3, price="200")

    exits = basket.take_profit_exits(Decimal("101.0"), Decimal("90"))

    # Group 1 is both Entries, sold together at the group's own target.
    assert [(exit.entry.entry_number, exit.price) for exit in exits] == [
        (1, Decimal("101.0")),
        (2, Decimal("101.0")),
    ]


def test_the_group_the_market_reached_first_is_sold_first() -> None:
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="110")

    exits = basket.take_profit_exits(Decimal("116.0"), Decimal("100"))

    assert [(exit.entry.entry_number, exit.price) for exit in exits] == [
        (1, Decimal("106.0")),
        (2, Decimal("116.0")),
    ]


def test_a_basket_target_takes_nothing_the_groups_did_not_take() -> None:
    """There is no net under the groups: a group holds until it reaches its own."""
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="200")

    # 150 + 3 x 2 = 156 would once have swept both Entries out together.
    exits = basket.take_profit_exits(Decimal("156.0"), Decimal("100"))

    assert [exit.entry.entry_number for exit in exits] == [1]


def test_the_basket_target_is_the_nearest_of_its_groups() -> None:
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="200")

    assert basket.take_profit_price == Decimal("106.0")
    assert basket.reaches_take_profit(Decimal("106.0"), Decimal("100")) is True


def test_selling_an_entry_restates_the_target_of_the_group_it_left() -> None:
    basket = _basket()
    _fill(basket, 1, price="100", atr="2")
    _fill(basket, 2, price="90", atr="2")
    assert basket.entry_take_profit_price(1) == Decimal("101.0")

    basket.close_entry(
        2, exit_price=Decimal("101"), closed_at=datetime(2026, 1, 9, tzinfo=UTC)
    )

    # The group is the Entry bought at 100 again, but its target may only have
    # moved closer, so it stays where the cheaper Entry left it.
    assert basket.entry_take_profit_price(1) == Decimal("101.0")
    assert basket.take_profit_price == Decimal("101.0")


def test_a_group_the_last_entry_left_is_not_a_group_any_more() -> None:
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="200")

    basket.close_entry(
        1, exit_price=Decimal("106"), closed_at=datetime(2026, 1, 9, tzinfo=UTC)
    )

    assert basket.take_profit_price == Decimal("206.0")
    # The next Entry is measured against group 2, the only one still open.
    _fill(basket, 3, price="150")
    assert _groups(basket) == [2, 2]


def test_a_group_number_is_never_handed_to_a_later_group() -> None:
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="200")
    basket.close_entry(
        2, exit_price=Decimal("206"), closed_at=datetime(2026, 1, 9, tzinfo=UTC)
    )

    _fill(basket, 3, price="300")

    assert _groups(basket) == [1, 3]


def test_a_short_entry_averages_down_by_being_sold_higher() -> None:
    basket = _basket(PositionSide.SHORT)
    _fill(basket, 1, price="100", side=PositionSide.SHORT)
    _fill(basket, 2, price="110", side=PositionSide.SHORT)

    assert _groups(basket) == [1, 1]
    # Sold at 105 on average, bought back 6 lower.
    assert basket.entry_take_profit_price(1) == Decimal("99.0")

    _fill(basket, 3, price="90", side=PositionSide.SHORT)

    assert _groups(basket) == [1, 1, 2]
    assert basket.entry_take_profit_price(3) == Decimal("84.0")


def test_a_short_group_target_moves_closer_but_never_further_away() -> None:
    basket = _basket(PositionSide.SHORT)
    _fill(basket, 1, price="100", atr="2", side=PositionSide.SHORT)
    assert basket.entry_take_profit_price(1) == Decimal("94.0")

    # 100.5 - 3 x 8 = 76.5 on its own, which is further away than 94.
    _fill(basket, 2, price="101", atr="8", side=PositionSide.SHORT)

    assert basket.entry_take_profit_price(1) == Decimal("94.0")


def test_every_open_entry_is_shown_with_the_group_it_belongs_to() -> None:
    basket = _basket()
    _fill(basket, 1, price="100")
    _fill(basket, 2, price="90")
    _fill(basket, 3, price="200")

    assert [
        (view.entry_number, view.group_id, view.take_profit_price)
        for view in basket.open_entry_targets()
    ] == [
        (1, 1, Decimal("101.0")),
        (2, 1, Decimal("101.0")),
        (3, 2, Decimal("206.0")),
    ]


def test_a_replayed_basket_takes_back_the_groups_and_targets_it_had() -> None:
    """Ratchet and membership are history, so neither is worked out again."""
    basket = _basket()
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
        entry_number=4,
        group_id=3,
    )
    basket.add_entry(
        price=Decimal("120"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 2, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
        entry_number=5,
        group_id=4,
    )
    basket.restore_group_take_profit_prices({3: Decimal("104.0"), 4: Decimal("125.0")})

    assert basket.entry_take_profit_price(4) == Decimal("104.0")
    assert basket.entry_take_profit_price(5) == Decimal("125.0")
    # A new Entry carries on from the group numbers history handed back.
    _fill(basket, 3, price="110")
    assert _groups(basket) == [3, 4, 4]


def test_a_group_falls_back_to_the_atr_of_the_entry_it_still_holds() -> None:
    """The Entry that brought the latest measurement in can be the one that goes."""
    basket = _basket()
    _fill(basket, 1, price="100", atr="2", quantity="3")
    _fill(basket, 2, price="90", atr="1", quantity="1")
    # The Group paid 97.5 on average and measures with the latest Fill's ATR.
    assert basket.entry_take_profit_price(1) == Decimal("100.5")

    basket.close_entry(
        2, exit_price=Decimal("100.5"), closed_at=datetime(2026, 1, 9, tzinfo=UTC)
    )

    # Back to one Entry at 100, and to the ATR that Entry was read against —
    # 106, which is further away, so the Group holds the price it had.
    assert basket.entry_take_profit_price(1) == Decimal("100.5")


def test_a_group_target_is_rounded_against_the_seller_on_both_sides() -> None:
    basket = _basket(PositionSide.SHORT)
    _fill(basket, 1, price="100", atr="1.03", quantity="1", side=PositionSide.SHORT)
    # 100 - 3 x 1.03 = 96.91, rounded up against the seller.
    assert basket.entry_take_profit_price(1) == Decimal("97.0")

    _fill(basket, 2, price="110", atr="1.03", quantity="3", side=PositionSide.SHORT)

    assert basket.average_entry_price == Decimal("107.5")
    # One Group, sold at 107.5 on average: 107.5 - 3 x 1.03 = 104.41, rounded
    # up against the seller. A Short buys back above, so 104.5 is the move
    # closer that averaging up the Group is allowed to make.
    assert basket.entry_take_profit_price(1) == Decimal("104.5")
