from decimal import Decimal
from enum import StrEnum


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


def unrealized_pnl(
    *,
    side: PositionSide,
    average_entry_price: Decimal,
    quantity: Decimal,
    current_price: Decimal,
) -> Decimal:
    if not isinstance(side, PositionSide):
        raise ValueError("side must be a PositionSide")
    _require_positive(average_entry_price, "average_entry_price")
    _require_positive(quantity, "quantity")
    _require_positive(current_price, "current_price")
    if side is PositionSide.LONG:
        return (current_price - average_entry_price) * quantity
    return (average_entry_price - current_price) * quantity


def account_equity(
    *,
    side: PositionSide,
    average_entry_price: Decimal,
    quantity: Decimal,
    available_capital: Decimal,
    current_price: Decimal,
) -> Decimal:
    """What the account is worth with the position marked to a price.

    The wallet plus whatever the open position is currently worth — the one
    number that says how much of the capital a Futures Session put at risk is
    still there.
    """
    _require_positive(available_capital, "available_capital")
    marked_pnl = unrealized_pnl(
        side=side,
        average_entry_price=average_entry_price,
        quantity=quantity,
        current_price=current_price,
    )
    return available_capital + marked_pnl


def _require_positive(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")
