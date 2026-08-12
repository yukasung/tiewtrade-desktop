from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from tiewtrade.strategies.fast_test.preset import FastTestPreset


def test_preset_v1_shortens_the_warm_up_without_shortening_the_target() -> None:
    preset = FastTestPreset.v1()

    assert preset.version == "fast-test-v1"
    assert preset.rsi_period == 2
    assert preset.atr_period == 2
    assert preset.stop_loss_atr_multiplier is None
    # Three candles of warm-up against fifteen for rsi-step-grid-v1.
    assert preset.minimum_warm_up_candles == 3
    # A target inside one candle's range closes the Basket before a second
    # Entry can join it, which is the case this preset exists to produce.
    assert preset.take_profit_atr_multiplier == Decimal("3")


def test_preset_is_immutable() -> None:
    preset = FastTestPreset.v1()

    with pytest.raises(FrozenInstanceError):
        preset.rsi_period = 7  # type: ignore[misc]


def test_preset_rejects_a_stop_loss_it_does_not_run() -> None:
    with pytest.raises(ValueError, match="stop_loss_atr_multiplier"):
        FastTestPreset(
            version="fast-test-v1",
            rsi_period=2,
            atr_period=2,
            take_profit_atr_multiplier=Decimal("3"),
            stop_loss_atr_multiplier=Decimal("1"),
        )


def test_preset_version_cannot_be_reused_with_different_parameters() -> None:
    with pytest.raises(ValueError, match="fast-test-v1 parameters"):
        FastTestPreset(
            version="fast-test-v1",
            rsi_period=7,
            atr_period=2,
            take_profit_atr_multiplier=Decimal("3"),
        )
