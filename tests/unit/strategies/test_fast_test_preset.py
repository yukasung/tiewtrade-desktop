from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from tiewtrade.strategies.fast_test.preset import FastTestPreset
from tiewtrade.strategies.rsi_buy_only.preset import RsiBuyOnlyPreset


def test_preset_v1_measures_the_way_a_traded_preset_measures() -> None:
    preset = FastTestPreset.v1()
    traded = RsiBuyOnlyPreset.v1()

    assert preset.version == "fast-test-v1"
    # Both periods are the fourteen every traded preset reads, so a target
    # taken off this preset means what a target means anywhere else.
    assert preset.rsi_period == traded.rsi_period == 14
    assert preset.atr_period == traded.atr_period == 14
    assert preset.take_profit_atr_multiplier == Decimal("3")
    assert preset.stop_loss_atr_multiplier == Decimal("3")
    assert preset.minimum_warm_up_candles == 15


def test_preset_is_immutable() -> None:
    preset = FastTestPreset.v1()

    with pytest.raises(FrozenInstanceError):
        preset.rsi_period = 7  # type: ignore[misc]


def test_preset_rejects_a_non_positive_stop_loss() -> None:
    with pytest.raises(ValueError, match="stop_loss_atr_multiplier must be positive"):
        FastTestPreset(
            version="fast-test-v1",
            rsi_period=14,
            atr_period=14,
            take_profit_atr_multiplier=Decimal("3"),
            stop_loss_atr_multiplier=Decimal("0"),
        )


def test_preset_version_cannot_be_reused_with_different_parameters() -> None:
    with pytest.raises(ValueError, match="fast-test-v1 parameters"):
        FastTestPreset(
            version="fast-test-v1",
            rsi_period=7,
            atr_period=14,
            take_profit_atr_multiplier=Decimal("3"),
            stop_loss_atr_multiplier=Decimal("3"),
        )


def test_preset_version_cannot_drop_the_stop_loss_it_now_runs() -> None:
    with pytest.raises(ValueError, match="fast-test-v1 parameters"):
        FastTestPreset(
            version="fast-test-v1",
            rsi_period=14,
            atr_period=14,
            take_profit_atr_multiplier=Decimal("3"),
            stop_loss_atr_multiplier=None,
        )
