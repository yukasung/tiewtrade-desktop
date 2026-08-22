"""What a candle must be to count towards a warm-up.

Warming up is replaying history into a reader that has not seen any yet, so
every candle offered has to be one the stream had not already taken: a
duplicate, a candle out of order, or one that has not closed would leave the
reader warmed to a past that never happened, and an indicator read off it is
wrong in a way nothing downstream can notice.

The rule lives here rather than at each place that warms something up,
because every one of them is refusing the same thing for the same reason.
What each does with a candle it accepted is its own business — a Session
feeds its indicators, a pipeline only counts — so the loop stays with the
caller and only the refusal is shared.
"""

from datetime import datetime

from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.completed_candle_stream import (
    CandleAcceptance,
    CompletedCandleStream,
)


def require_warm_up_candle(
    stream: CompletedCandleStream,
    candle: Candle,
    received_at: datetime,
) -> None:
    """Offer one candle to the stream, refusing any it does not take as new."""
    if stream.accept(candle, received_at) is not CandleAcceptance.ACCEPTED:
        raise ValueError("warm-up requires new completed candles")
