from hashlib import sha256
from uuid import UUID

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.fast_test.preset import FastTestPreset
from tiewtrade.strategies.indicators import IndicatorSnapshot
from tiewtrade.trading.position import PositionSide

# How far apart two Fills of this preset stand, counted in candles: an Entry
# that filled on one candle is followed by one that fills three candles later.
# Without it the preset stacks a second Entry into the open Basket on the very
# next candle, at a price the market has had no time to move away from.
CANDLES_BETWEEN_ENTRIES = 3


class FastTestStrategy:
    """Enters on the first candle it is allowed to, and every third one after.

    Built to exercise the machinery, not to trade it. RsiStepGridStrategy has
    to see RSI fall past its reset and climb back on a rising candle, which on
    a live market can be hours of waiting before anything appears on screen;
    this one reads no market condition at all, so an Entry, a Basket, a Fill
    and a take profit can all be watched within a few candles.

    Reading no market condition is what makes it spacing's problem, not the
    market's, to keep two Entries apart. RsiStepGridStrategy clears its reset
    on a Fill, so its next Entry waits for a whole RSI cycle; here the wait is
    counted in candles instead, and nothing else stands between two Entries.

    What it keeps from RsiStepGridStrategy is everything that is not the
    signal: one pending Intent at a time, an intent_id derived from the
    decision inputs so the same candle cannot enter twice, the Entry Pair gate
    honoured through can_enter, and LONG-only Entries carrying the ATR the
    take profit is measured from.
    """

    def __init__(self, session_id: UUID, preset: FastTestPreset) -> None:
        self._session_id = session_id
        self._preset = preset
        self._pending_intent: EntryIntent | None = None
        # Where the next Fill would land, counted from the last one. An Intent
        # read from this candle fills on the next, so the first candle after a
        # Fill already stands one candle away. None until anything has filled.
        self._next_fill_distance: int | None = None

    def evaluate(
        self,
        candle: Candle,
        indicators: IndicatorSnapshot,
        entry_number: int,
        can_enter: bool,
    ) -> EntryIntent | None:
        if entry_number <= 0:
            raise ValueError("entry_number must be positive")
        if self._next_fill_distance is not None:
            self._next_fill_distance += 1
        if self._pending_intent is not None:
            return None
        # The gate is the domain's rule, not a signal, so it still decides.
        if not can_enter:
            return None
        if (
            self._next_fill_distance is not None
            and self._next_fill_distance < CANDLES_BETWEEN_ENTRIES
        ):
            return None

        intent = EntryIntent(
            intent_id=self._intent_id(candle, entry_number),
            session_id=self._session_id,
            preset_version=self._preset.version,
            entry_number=entry_number,
            signal_candle=candle,
            atr=indicators.atr,
            side=PositionSide.LONG,
        )
        self._pending_intent = intent
        return intent

    def on_entry_filled(self, intent_id: str) -> None:
        if self._pending_intent is None or self._pending_intent.intent_id != intent_id:
            raise ValueError("fill does not match pending intent")
        self._pending_intent = None
        self._next_fill_distance = 0

    def on_entry_rejected(self, intent_id: str) -> None:
        if self._pending_intent is None or self._pending_intent.intent_id != intent_id:
            raise ValueError("rejection does not match pending intent")
        self._pending_intent = None

    def _intent_id(self, candle: Candle, entry_number: int) -> str:
        identity = "\n".join(
            (
                str(self._session_id),
                self._preset.version,
                candle.symbol,
                candle.timeframe,
                candle.open_time.isoformat(),
                str(entry_number),
            )
        )
        return sha256(identity.encode("utf-8")).hexdigest()
