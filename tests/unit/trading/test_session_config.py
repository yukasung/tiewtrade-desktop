from dataclasses import FrozenInstanceError, fields
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import (
    MarketType,
    SessionConfig,
    TradeMode,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000010")


def make_config(**overrides: object) -> SessionConfig:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "preset_version": "rsi-step-grid-v1",
        "market_type": MarketType.SPOT,
        "trade_mode": TradeMode.PAPER,
        "capital_per_entry": Decimal("1000"),
        "entry_policy": EntryPolicy(max_entries=10),
        "futures_policy": None,
    }
    values.update(overrides)
    return SessionConfig(**values)  # type: ignore[arg-type]


def test_session_configuration_is_owned_directly_by_the_session() -> None:
    field_names = {field.name for field in fields(SessionConfig)}

    assert "session_id" in field_names
    assert "account_profile_id" not in field_names


@pytest.mark.parametrize("trade_mode", [TradeMode.PAPER, TradeMode.LIVE])
@pytest.mark.parametrize("market_type", [MarketType.SPOT, MarketType.FUTURES])
def test_session_configuration_supports_each_mode_and_market(
    trade_mode: TradeMode, market_type: MarketType
) -> None:
    config = make_config(
        trade_mode=trade_mode,
        market_type=market_type,
        futures_policy=(
            FuturesTradingPolicy.v1(leverage=3)
            if market_type is MarketType.FUTURES
            else None
        ),
    )

    assert config.trade_mode is trade_mode
    assert config.market_type is market_type


def test_spot_session_owns_only_the_entry_policy_it_was_given() -> None:
    """Spot has no policy of its own; it trades the capital under its Entries."""
    entry_policy = EntryPolicy(max_entries=12)

    config = make_config(entry_policy=entry_policy)

    assert config.entry_policy is entry_policy
    assert "spot_policy" not in {field.name for field in fields(SessionConfig)}


def test_futures_session_requires_futures_policy() -> None:
    with pytest.raises(ValueError, match="futures_policy"):
        make_config(market_type=MarketType.FUTURES)


def test_spot_session_rejects_futures_policy() -> None:
    with pytest.raises(ValueError, match="futures_policy"):
        make_config(futures_policy=FuturesTradingPolicy.v1(leverage=3))


def test_session_configuration_is_immutable() -> None:
    config = make_config()

    with pytest.raises(FrozenInstanceError):
        config.capital_per_entry = Decimal("2000")  # type: ignore[misc]


@pytest.mark.parametrize("capital", [Decimal("0"), Decimal("-1")])
def test_session_requires_positive_capital(capital: Decimal) -> None:
    with pytest.raises(ValueError, match="capital_per_entry"):
        make_config(capital_per_entry=capital)


def test_live_configuration_is_only_data_and_does_not_start_execution() -> None:
    config = make_config(trade_mode=TradeMode.LIVE)

    assert config.trade_mode is TradeMode.LIVE
