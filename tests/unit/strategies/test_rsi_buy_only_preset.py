from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from tiewtrade.strategies.rsi_buy_only.preset import RsiBuyOnlyPreset


def test_preset_v1_matches_the_approved_product_parameters() -> None:
    preset = RsiBuyOnlyPreset.v1()

    assert preset.version == "rsi-buy-only-v1"
    assert preset.rsi_period == 14
    assert preset.rsi_buy_threshold == Decimal("30")
    assert preset.atr_period == 14
    assert preset.take_profit_atr_multiplier == Decimal("1.5")
    assert preset.stop_loss_atr_multiplier == Decimal("1.5")
    assert preset.minimum_warm_up_candles == 15


def test_preset_is_immutable() -> None:
    preset = RsiBuyOnlyPreset.v1()

    with pytest.raises(FrozenInstanceError):
        preset.rsi_period = 7  # type: ignore[misc]


def test_preset_rejects_out_of_range_rsi_threshold() -> None:
    with pytest.raises(ValueError, match="rsi_buy_threshold"):
        RsiBuyOnlyPreset(
            version="invalid",
            rsi_period=14,
            rsi_buy_threshold=Decimal("101"),
            atr_period=14,
            take_profit_atr_multiplier=Decimal("1.5"),
            stop_loss_atr_multiplier=Decimal("1.5"),
        )


def test_preset_rejects_non_positive_stop_loss_multiplier() -> None:
    with pytest.raises(ValueError, match="stop_loss_atr_multiplier"):
        RsiBuyOnlyPreset(
            version="invalid",
            rsi_period=14,
            rsi_buy_threshold=Decimal("30"),
            atr_period=14,
            take_profit_atr_multiplier=Decimal("1.5"),
            stop_loss_atr_multiplier=Decimal("0"),
        )


def test_preset_version_cannot_be_reused_with_different_parameters() -> None:
    with pytest.raises(ValueError, match="rsi-buy-only-v1 parameters"):
        RsiBuyOnlyPreset(
            version="rsi-buy-only-v1",
            rsi_period=7,
            rsi_buy_threshold=Decimal("30"),
            atr_period=14,
            take_profit_atr_multiplier=Decimal("1.5"),
            stop_loss_atr_multiplier=Decimal("1.5"),
        )
