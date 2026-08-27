from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from tiewtrade.strategies.fast_test.preset import FastTestPreset
from tiewtrade.strategies.fast_test.strategy import FastTestStrategy
from tiewtrade.strategies.protocols import EntryStrategy, StrategyPreset
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.strategies.rsi_step_grid.strategy import RsiStepGridStrategy
from tiewtrade.trading.session_config import MarketType


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    preset_version: str
    display_name: str
    market_types: frozenset[MarketType]
    build_preset: Callable[[], StrategyPreset]
    build_strategy: Callable[[UUID, StrategyPreset], EntryStrategy]


def _build_rsi_step_grid_strategy(
    session_id: UUID, preset: StrategyPreset
) -> EntryStrategy:
    assert isinstance(preset, RsiStepGridPreset)
    return RsiStepGridStrategy(session_id, preset)


def _build_fast_test_strategy(
    session_id: UUID, preset: StrategyPreset
) -> EntryStrategy:
    assert isinstance(preset, FastTestPreset)
    return FastTestStrategy(session_id, preset)


_DEFINITIONS: dict[str, StrategyDefinition] = {
    RsiStepGridPreset.v1().version: StrategyDefinition(
        preset_version=RsiStepGridPreset.v1().version,
        display_name="RSI Step Grid v1",
        market_types=frozenset({MarketType.SPOT, MarketType.FUTURES}),
        build_preset=RsiStepGridPreset.v1,
        build_strategy=_build_rsi_step_grid_strategy,
    ),
    # Named for what it is on every screen that lists it: a way to see the
    # machinery run, not a strategy anyone should trade.
    FastTestPreset.v1().version: StrategyDefinition(
        preset_version=FastTestPreset.v1().version,
        display_name="Fast Test (testing only)",
        market_types=frozenset({MarketType.SPOT, MarketType.FUTURES}),
        build_preset=FastTestPreset.v1,
        build_strategy=_build_fast_test_strategy,
    ),
}


def strategy_definitions() -> tuple[StrategyDefinition, ...]:
    return tuple(_DEFINITIONS.values())


def strategy_definitions_for(market_type: MarketType) -> tuple[StrategyDefinition, ...]:
    return tuple(
        definition
        for definition in _DEFINITIONS.values()
        if market_type in definition.market_types
    )


def strategy_definition_for(preset_version: str) -> StrategyDefinition:
    try:
        return _DEFINITIONS[preset_version]
    except KeyError as error:
        raise ValueError(
            f"unknown strategy preset_version: {preset_version!r}"
        ) from error
