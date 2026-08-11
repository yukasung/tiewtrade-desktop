from uuid import UUID

import pytest

from tiewtrade.strategies.registry import (
    strategy_definition_for,
    strategy_definitions,
    strategy_definitions_for,
)
from tiewtrade.strategies.rsi_buy_only.preset import RsiBuyOnlyPreset
from tiewtrade.strategies.rsi_buy_only.strategy import RsiBuyOnlyStrategy
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.strategies.rsi_step_grid.strategy import RsiStepGridStrategy
from tiewtrade.trading.session_config import MarketType


def test_strategy_definitions_lists_every_registered_strategy() -> None:
    versions = {definition.preset_version for definition in strategy_definitions()}

    assert versions == {"rsi-step-grid-v1", "rsi-buy-only-v1"}


def test_strategy_definition_for_resolves_step_grid() -> None:
    definition = strategy_definition_for("rsi-step-grid-v1")

    assert definition.display_name == "RSI Step Grid v1"
    assert definition.market_types == frozenset({MarketType.SPOT, MarketType.FUTURES})
    assert isinstance(definition.build_preset(), RsiStepGridPreset)
    assert isinstance(
        definition.build_strategy(UUID(int=0), definition.build_preset()),
        RsiStepGridStrategy,
    )


def test_strategy_definition_for_resolves_buy_only() -> None:
    definition = strategy_definition_for("rsi-buy-only-v1")

    assert definition.display_name == "RSI Buy Only v1"
    assert definition.market_types == frozenset({MarketType.SPOT})
    assert isinstance(definition.build_preset(), RsiBuyOnlyPreset)
    assert isinstance(
        definition.build_strategy(UUID(int=0), definition.build_preset()),
        RsiBuyOnlyStrategy,
    )


def test_strategy_definition_for_raises_on_unknown_version() -> None:
    with pytest.raises(ValueError, match="unknown strategy preset_version"):
        strategy_definition_for("does-not-exist")


def test_strategy_definitions_for_filters_by_market_type() -> None:
    spot_versions = {
        definition.preset_version
        for definition in strategy_definitions_for(MarketType.SPOT)
    }
    futures_versions = {
        definition.preset_version
        for definition in strategy_definitions_for(MarketType.FUTURES)
    }

    assert spot_versions == {"rsi-step-grid-v1", "rsi-buy-only-v1"}
    assert futures_versions == {"rsi-step-grid-v1"}
