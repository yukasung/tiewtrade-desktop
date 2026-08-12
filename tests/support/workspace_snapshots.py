from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tiewtrade.application.trading_workspace import BasketEntryGate, BasketSnapshot

OBSERVED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def basket_snapshot(**overrides: Any) -> BasketSnapshot:
    """An open Spot Basket, with only the fields a test cares about spelled out."""
    basket = BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=2,
        total_quantity=Decimal("1"),
        average_entry_price=Decimal("64000"),
        current_price=Decimal("65000"),
        take_profit_price=Decimal("66000"),
        unrealized_pnl=Decimal("1000"),
        liquidation_price=None,
        entry_gate=BasketEntryGate.OPEN,
        cooldown_until=None,
        updated_at_utc=OBSERVED_AT,
    )
    return replace(basket, **overrides) if overrides else basket
