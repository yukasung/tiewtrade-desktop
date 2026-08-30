"""What every Strategy Preset must be true of, whatever else it says.

A preset names the indicators it reads and the distance it takes profit at.
Those are the parts every strategy has, so they are checked here rather than
restated in each one: a preset that agreed with the others on the rules but
not on, say, whether a zero ATR period is allowed would be a Session that
starts and then reads a measurement that does not exist.

Only the shared parts live here. What makes a preset itself — a threshold to
enter at, a grid to step down, a version that must stay canonical — belongs
to the preset, and stays there.
"""

from decimal import Decimal

from tiewtrade.strategies.indicators import WILDER_WARM_UP_CANDLES


def require_shared_preset_rules(
    *,
    version: str,
    rsi_period: int,
    atr_period: int,
    take_profit_atr_multiplier: Decimal,
) -> None:
    if not version:
        raise ValueError("version must not be empty")
    if rsi_period <= 0:
        raise ValueError("rsi_period must be positive")
    if atr_period <= 0:
        raise ValueError("atr_period must be positive")
    if take_profit_atr_multiplier <= 0:
        raise ValueError("take_profit_atr_multiplier must be positive")


def minimum_warm_up_candles(rsi_period: int, atr_period: int) -> int:
    """How many completed candles a preset needs before it may read anything.

    RSI asks for one more than its period because the first candle only
    establishes the price the second one moved from. The longer of the two
    wins: a Session that started on the shorter would be reading one
    indicator that is ready and one that is still filling.

    Having a reading is not the same as having the market's reading, so the
    Wilder warm-up sets the floor. Below it a Session reads its own seed for
    long enough to act on it.
    """
    return max(rsi_period + 1, atr_period, WILDER_WARM_UP_CANDLES)
