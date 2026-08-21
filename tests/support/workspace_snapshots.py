from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketEntrySnapshot,
    BasketSnapshot,
)

OBSERVED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def basket_entries(
    *prices: str,
    quantity: str = "0.5",
    current_price: str = "65000",
    first_filled_at: datetime = OBSERVED_AT,
    take_profit_offset: str = "1000",
) -> tuple[BasketEntrySnapshot, ...]:
    """Entries numbered as they were taken, each with its own price and target."""
    mark = Decimal(current_price)
    offset = Decimal(take_profit_offset)
    return tuple(
        BasketEntrySnapshot(
            entry_number=number,
            filled_at_utc=first_filled_at + timedelta(hours=number - 1),
            price=Decimal(price),
            quantity=Decimal(quantity),
            notional=Decimal(price) * Decimal(quantity),
            unrealized_pnl=(mark - Decimal(price)) * Decimal(quantity),
            take_profit_price=Decimal(price) + offset,
        )
        for number, price in enumerate(prices, start=1)
    )


def basket_snapshot(**overrides: Any) -> BasketSnapshot:
    """An open Spot Basket, with only the fields a test cares about spelled out."""
    basket = BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=2,
        entries=basket_entries("63000", "65000"),
        total_quantity=Decimal("1"),
        average_entry_price=Decimal("64000"),
        current_price=Decimal("65000"),
        take_profit_price=Decimal("66000"),
        unrealized_pnl=Decimal("1000"),
        liquidation_price=None,
        entry_gate=BasketEntryGate.OPEN,
        updated_at_utc=OBSERVED_AT,
    )
    return replace(basket, **overrides) if overrides else basket
