from decimal import Decimal

import pytest

from tiewtrade.trading.position import (
    PositionSide,
    account_equity,
    unrealized_pnl,
)


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        (PositionSide.LONG, Decimal("6")),
        (PositionSide.SHORT, Decimal("-6")),
    ],
)
def test_unrealized_pnl_is_side_aware(
    side: PositionSide,
    expected: Decimal,
) -> None:
    assert (
        unrealized_pnl(
            side=side,
            average_entry_price=Decimal("100"),
            quantity=Decimal("2"),
            current_price=Decimal("103"),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("average_entry_price", Decimal("0")),
        ("quantity", Decimal("0")),
        ("current_price", Decimal("NaN")),
    ],
)
def test_unrealized_pnl_rejects_invalid_position_facts(
    field: str,
    value: Decimal,
) -> None:
    values = {
        "average_entry_price": Decimal("100"),
        "quantity": Decimal("2"),
        "current_price": Decimal("103"),
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        unrealized_pnl(side=PositionSide.LONG, **values)


def test_account_equity_is_capital_plus_the_marked_result() -> None:
    assert account_equity(
        side=PositionSide.LONG,
        average_entry_price=Decimal("100"),
        quantity=Decimal("3000"),
        available_capital=Decimal("200000"),
        current_price=Decimal("90"),
    ) == Decimal("170000")


def test_account_equity_follows_the_side_the_position_took() -> None:
    assert account_equity(
        side=PositionSide.SHORT,
        average_entry_price=Decimal("100"),
        quantity=Decimal("2"),
        available_capital=Decimal("1000"),
        current_price=Decimal("110"),
    ) == Decimal("980")


def test_account_equity_can_be_spent_down_to_nothing() -> None:
    """A position may lose exactly what the wallet held, and then some."""
    assert account_equity(
        side=PositionSide.LONG,
        average_entry_price=Decimal("100"),
        quantity=Decimal("1"),
        available_capital=Decimal("10"),
        current_price=Decimal("90"),
    ) == Decimal("0")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("available_capital", Decimal("0")),
        ("available_capital", Decimal("NaN")),
        ("average_entry_price", Decimal("0")),
        ("quantity", Decimal("Infinity")),
        ("current_price", Decimal("-1")),
    ],
)
def test_account_equity_rejects_invalid_inputs(field: str, value: Decimal) -> None:
    inputs: dict[str, object] = {
        "side": PositionSide.LONG,
        "average_entry_price": Decimal("100"),
        "quantity": Decimal("1"),
        "available_capital": Decimal("1000"),
        "current_price": Decimal("100"),
    }
    inputs[field] = value

    with pytest.raises(ValueError, match=field):
        account_equity(**inputs)  # type: ignore[arg-type]
