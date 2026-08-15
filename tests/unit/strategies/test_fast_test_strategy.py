from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.fast_test.preset import FastTestPreset
from tiewtrade.strategies.fast_test.strategy import FastTestStrategy
from tiewtrade.strategies.indicators import IndicatorSnapshot
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


def strategy() -> FastTestStrategy:
    return FastTestStrategy(SESSION_ID, FastTestPreset.v1())


def test_the_first_candle_it_is_allowed_enters_with_no_confirmation() -> None:
    """No reset, no rising close: the point is not to wait."""
    signal_candle = candle(0, "102", "101")

    intent = strategy().evaluate(
        signal_candle,
        snapshot("29", "2.75"),
        entry_number=1,
        can_enter=True,
    )

    assert intent is not None
    assert intent.session_id == SESSION_ID
    assert intent.preset_version == "fast-test-v1"
    assert intent.entry_number == 1
    assert intent.signal_candle == signal_candle
    assert intent.atr == Decimal("2.75")
    assert intent.side is PositionSide.LONG


@pytest.mark.parametrize(
    ("rsi", "open_price", "close_price"),
    [
        ("5", "102", "101"),
        ("50", "100", "100"),
        ("95", "99", "104"),
    ],
)
def test_no_market_reading_can_hold_an_entry_back(
    rsi: str, open_price: str, close_price: str
) -> None:
    intent = strategy().evaluate(
        candle(5, open_price, close_price),
        snapshot(rsi),
        entry_number=1,
        can_enter=True,
    )

    assert intent is not None


def test_a_closed_gate_still_decides() -> None:
    """Entry Pair rules are the domain's, not a signal this preset may skip."""
    intent = strategy().evaluate(
        candle(5, "100", "102"),
        snapshot("51"),
        entry_number=1,
        can_enter=False,
    )

    assert intent is None


def test_a_filled_entry_stands_three_candles_from_the_next_one() -> None:
    """The Fill lands on candle 5; the next may not land before candle 20."""
    fast = strategy()
    first = fast.evaluate(
        candle(0, "100", "101"), snapshot("40"), entry_number=1, can_enter=True
    )
    assert first is not None

    # The Intent read from candle 0 fills at the open of candle 5.
    fast.on_entry_filled(first.intent_id)

    # The candle the Entry filled on, and the one after it, are both too close:
    # an Intent read from either would fill inside the three-candle gap.
    for minute in (5, 10):
        assert (
            fast.evaluate(
                candle(minute, "102", "103"),
                snapshot("42"),
                entry_number=2,
                can_enter=True,
            )
            is None
        )

    second = fast.evaluate(
        candle(15, "102", "103"), snapshot("42"), entry_number=2, can_enter=True
    )

    assert second is not None
    assert second.intent_id != first.intent_id


def test_a_pending_intent_does_not_stop_the_wait_from_running_down() -> None:
    """The candles an Intent waits out are candles the market moved through."""
    fast = strategy()
    first = fast.evaluate(
        candle(0, "100", "101"), snapshot("40"), entry_number=1, can_enter=True
    )
    assert first is not None
    fast.on_entry_filled(first.intent_id)

    fast.evaluate(
        candle(5, "101", "102"), snapshot("41"), entry_number=2, can_enter=True
    )
    # A gate the domain closed does not pause the count either.
    fast.evaluate(
        candle(10, "102", "103"), snapshot("42"), entry_number=2, can_enter=False
    )

    assert (
        fast.evaluate(
            candle(15, "103", "104"), snapshot("43"), entry_number=2, can_enter=True
        )
        is not None
    )


def test_a_rejected_entry_frees_the_next_candle() -> None:
    fast = strategy()
    first = fast.evaluate(
        candle(0, "100", "101"), snapshot("40"), entry_number=1, can_enter=True
    )
    assert first is not None

    fast.on_entry_rejected(first.intent_id)

    assert (
        fast.evaluate(
            candle(5, "101", "102"), snapshot("41"), entry_number=1, can_enter=True
        )
        is not None
    )


def test_same_decision_inputs_create_the_same_intent_id() -> None:
    signal_candle = candle(5, "100", "102")

    first = strategy().evaluate(
        signal_candle, snapshot("51"), entry_number=1, can_enter=True
    )
    second = strategy().evaluate(
        signal_candle, snapshot("51"), entry_number=1, can_enter=True
    )

    assert first is not None
    assert second is not None
    assert first.intent_id == second.intent_id


def test_fill_must_match_the_pending_intent() -> None:
    fast = strategy()
    intent = fast.evaluate(
        candle(5, "100", "102"), snapshot("51"), entry_number=1, can_enter=True
    )
    assert intent is not None

    with pytest.raises(ValueError, match="pending intent"):
        fast.on_entry_filled("wrong-id")


def test_entry_number_must_be_positive() -> None:
    with pytest.raises(ValueError, match="entry_number must be positive"):
        strategy().evaluate(
            candle(5, "100", "102"), snapshot("51"), entry_number=0, can_enter=True
        )
