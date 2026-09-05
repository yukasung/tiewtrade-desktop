"""What one candle would leave a Paper Session holding, if it gets that far.

Every part a candle can move is worked on as a copy and taken up in one go, so
a Session that raises half way through a candle is not left holding a Basket
that sold some of its Entries and none of what they realised.

Paper Spot and Paper Futures both move a candle this way, and the moves
themselves are Basket and Entry Pair rules rather than market ones: taking an
Intent back, taking one in, selling an Entry out of the Basket, and settling a
Basket the last sale emptied. Stated once here, so the two markets cannot
answer them differently. What each market keeps to itself is what it alone
holds — a wallet, the candle a sale between ticks owns — carried as its own
field on its own subclass.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.indicators import IndicatorSnapshot, WilderIndicators
from tiewtrade.strategies.protocols import EntryStrategy
from tiewtrade.trading.basket import (
    Basket,
    BasketCloseReason,
    ClosedBasket,
    ClosedEntry,
)
from tiewtrade.trading.entry_pair import EntryPairLifecycle


@dataclass(slots=True, kw_only=True)
class PaperCandleTransition:
    """The parts of a Paper Session one candle can move.

    Keyword-only, so a market adding a field of its own does not have to give
    it a default it would never use.
    """

    indicators: WilderIndicators
    strategy: EntryStrategy
    lifecycle: EntryPairLifecycle
    basket: Basket | None
    pending_intent: EntryIntent | None
    closed_basket_count: int

    def reject_pending_intent(self) -> None:
        """Give the Intent back to the strategy, which signals again when it can.

        Holding it instead would fill it at a later open than the candle it was
        read from, pricing an Entry off a signal the market has left behind.
        """
        intent = self.pending_intent
        if intent is None:
            return
        self.strategy.on_entry_rejected(intent.intent_id)
        self.pending_intent = None

    def accept_pending_intent(self, filled_at: datetime) -> None:
        """Take the Intent in, once the Entry it asked for has actually filled."""
        intent = self.pending_intent
        assert intent is not None
        self.lifecycle.record_fill(filled_at)
        self.strategy.on_entry_filled(intent.intent_id)
        self.pending_intent = None

    def evaluate_strategy(
        self,
        candle: Candle,
        indicators: IndicatorSnapshot | None,
        *,
        max_entries: int,
        exited_on_this_candle: bool,
    ) -> None:
        """Ask the strategy what this candle signals, and hold what it answers.

        Nothing is signalled before the indicators have a reading to be asked
        against, and nothing on the candle that emptied or lightened the
        Basket: a signal read there would fill at the very next open,
        re-entering on the back of its own exit. The earliest a new Entry can
        be signalled is the candle after the sale.
        """
        if indicators is None:
            return
        basket = self.basket
        can_enter = (
            not exited_on_this_candle
            and self.lifecycle.can_enter(candle.close_time)
            and (basket is None or basket.entry_count < max_entries)
        )
        # Entry numbers count every Entry the Basket has ever taken, so one
        # that sold does not hand its number to the Entry that replaces it —
        # which matters beyond the audit trail, an Intent being identified by
        # its number and a Preset reading it to place itself on the grid.
        entry_sequence = 0 if basket is None else basket.entry_sequence
        intent = self.strategy.evaluate(
            candle,
            indicators,
            entry_number=entry_sequence + 1,
            can_enter=can_enter,
        )
        if intent is not None:
            self.pending_intent = intent

    def next_basket_id(self, session_id: UUID) -> UUID:
        """The id the Basket this candle opens would take.

        Derived from the Session and how many Baskets it has already closed,
        so the same Session replayed from the same candles names the same
        Baskets.
        """
        return uuid5(session_id, f"basket:{self.closed_basket_count + 1}")

    def sell_entry(
        self,
        entry_number: int,
        *,
        exit_price: Decimal,
        closed_at: datetime,
    ) -> ClosedEntry:
        """Take one Entry out of the Basket, at the price it went for.

        The Entry hands its place in the Basket back, so the quota and the
        Entry Pair count it was using are free again — whether a target sold
        it or the trader did.
        """
        assert self.basket is not None
        closed_entry = self.basket.close_entry(
            entry_number,
            exit_price=exit_price,
            closed_at=closed_at,
        )
        self.lifecycle.release_fill(entry_number)
        return closed_entry

    def settle_emptied_basket(
        self,
        *,
        exit_price: Decimal,
        closed_at: datetime,
        close_reason: BasketCloseReason,
    ) -> ClosedBasket | None:
        """The Basket's own record, where the last sale left it with nothing.

        None where Entries are still held: a Basket closes when its last Entry
        has gone, not when any one of them does. The sale that emptied it was
        already booked by the `sell_entry` that made it, so this record sells
        nothing itself.
        """
        assert self.basket is not None
        if not self.basket.is_empty:
            return None
        settled = self.basket.settled_result(
            exit_price=exit_price,
            closed_at=closed_at,
            close_reason=close_reason,
        )
        self.retire_basket()
        return settled

    def retire_basket(self) -> None:
        """Put the closed Basket down and count it, whatever closed it."""
        self.basket = None
        self.lifecycle.reset()
        self.closed_basket_count += 1
