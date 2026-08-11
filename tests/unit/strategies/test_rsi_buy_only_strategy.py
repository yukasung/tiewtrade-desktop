from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.indicators import IndicatorSnapshot
from tiewtrade.strategies.rsi_buy_only.preset import RsiBuyOnlyPreset
from tiewtrade.strategies.rsi_buy_only.strategy import RsiBuyOnlyStrategy
from tiewtrade.trading.position import PositionSide

SESSION_ID = UUID("00000000-0000-0000-0000-000000000078")


def candle(minute: int, open_price: str, close_price: str) -> Candle:
    high = max(Decimal(open_price), Decimal(close_price)) + Decimal("1")
    low = min(Decimal(open_price), Decimal(close_price)) - Decimal("1")
    return Candle(
        symbol="CONFIGURED_SYMBOL",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=Decimal(open_price),
        high=high,
        low=low,
        close=Decimal(close_price),
    )


def snapshot(rsi: str, atr: str = "2.5") -> IndicatorSnapshot:
    return IndicatorSnapshot(rsi=Decimal(rsi), atr=Decimal(atr))


def new_strategy() -> RsiBuyOnlyStrategy:
    return RsiBuyOnlyStrategy(SESSION_ID, RsiBuyOnlyPreset.v1())


def test_rsi_above_threshold_with_no_open_position_creates_an_intent() -> None:
    signal_candle = candle(5, "100", "102")

    intent = new_strategy().evaluate(
        signal_candle,
        snapshot("31", "2.5"),
        entry_number=1,
        can_enter=True,
    )

    assert intent is not None
    assert intent.session_id == SESSION_ID
    assert intent.preset_version == "rsi-buy-only-v1"
    assert intent.entry_number == 1
    assert intent.signal_candle == signal_candle
    assert intent.atr == Decimal("2.5")
    assert intent.side is PositionSide.LONG
    assert (
        intent.intent_id
        == "3e585b1afc6da7e038c302b92b76ec1903a6baf8d48b346349077b22ee402c12"
    )


def test_same_decision_inputs_create_the_same_intent_id() -> None:
    signal_candle = candle(5, "100", "102")

    first = new_strategy().evaluate(
        signal_candle, snapshot("31"), entry_number=1, can_enter=True
    )
    second = new_strategy().evaluate(
        signal_candle, snapshot("31"), entry_number=1, can_enter=True
    )

    assert first is not None
    assert second is not None
    assert first.intent_id == second.intent_id


@pytest.mark.parametrize(
    ("rsi", "entry_number", "can_enter"),
    [
        ("30", 1, True),
        ("29", 1, True),
        ("31", 2, True),
        ("31", 1, False),
    ],
)
def test_entry_requires_rsi_above_threshold_no_open_position_and_can_enter(
    rsi: str,
    entry_number: int,
    can_enter: bool,
) -> None:
    intent = new_strategy().evaluate(
        candle(5, "100", "102"),
        snapshot(rsi),
        entry_number=entry_number,
        can_enter=can_enter,
    )

    assert intent is None


def test_pending_intent_blocks_duplicates_and_fill_allows_the_next_signal() -> None:
    strategy = new_strategy()
    first = strategy.evaluate(
        candle(5, "100", "102"),
        snapshot("31"),
        entry_number=1,
        can_enter=True,
    )
    assert first is not None

    assert (
        strategy.evaluate(
            candle(10, "102", "104"),
            snapshot("60"),
            entry_number=1,
            can_enter=True,
        )
        is None
    )

    strategy.on_entry_filled(first.intent_id)

    second = strategy.evaluate(
        candle(15, "104", "106"),
        snapshot("40"),
        entry_number=1,
        can_enter=True,
    )
    assert second is not None
    assert second.intent_id != first.intent_id


def test_fill_must_match_the_pending_intent() -> None:
    strategy = new_strategy()
    intent = strategy.evaluate(
        candle(5, "100", "102"),
        snapshot("31"),
        entry_number=1,
        can_enter=True,
    )
    assert intent is not None

    with pytest.raises(ValueError, match="pending intent"):
        strategy.on_entry_filled("wrong-id")


def test_rejection_must_match_the_pending_intent() -> None:
    strategy = new_strategy()
    intent = strategy.evaluate(
        candle(5, "100", "102"),
        snapshot("31"),
        entry_number=1,
        can_enter=True,
    )
    assert intent is not None

    with pytest.raises(ValueError, match="pending intent"):
        strategy.on_entry_rejected("wrong-id")
