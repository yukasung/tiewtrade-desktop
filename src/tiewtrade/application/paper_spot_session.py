from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from tiewtrade.application.open_basket import OpenBasketState, reopen_basket
from tiewtrade.execution.paper_spot import (
    PaperSpotEntryFill,
    PaperSpotExecutor,
    PaperSpotExitFill,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.completed_candle_stream import (
    CandleAcceptance,
    CompletedCandleStream,
)
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.indicators import IndicatorSnapshot, WilderIndicators
from tiewtrade.strategies.protocols import EntryStrategy, StrategyPreset
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.basket import (
    Basket,
    BasketCloseReason,
    BasketEntry,
    ClosedBasket,
    ClosedEntry,
)
from tiewtrade.trading.entry_pair import EntryGate, EntryPairLifecycle
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules


class PaperSpotSessionState(StrEnum):
    ACTIVE = "active"
    FAILED_CLOSED = "failed_closed"


class PaperSpotFailureReason(StrEnum):
    EXECUTION_ERROR = "execution_error"


class PaperSpotSessionError(RuntimeError):
    """Paper Spot stopped because an execution invariant failed."""


@dataclass(frozen=True, slots=True)
class PaperSpotEntryExit:
    """One Entry sold at its own target, and what it left behind."""

    fill: PaperSpotExitFill
    closed: ClosedEntry


@dataclass(frozen=True, slots=True)
class PaperSpotSessionSnapshot:
    accepted: bool
    state: PaperSpotSessionState
    failure_reason: PaperSpotFailureReason | None
    pending_intent: EntryIntent | None
    entry_fill: PaperSpotEntryFill | None
    # The ATR the filled Entry was read against, which the Basket's take
    # profit was measured from. Present exactly when entry_fill is.
    entry_atr: Decimal | None
    # The number this Entry took in its Basket, which outlives the Entry.
    entry_number: int | None
    # The target this Entry alone is sold at. Present exactly when entry_fill is.
    entry_take_profit_price: Decimal | None
    # Entries sold at their own target. A candle can reach more than one.
    entry_exits: tuple[PaperSpotEntryExit, ...]
    # A Fill that took the whole position at once, which a stop loss does.
    exit_fill: PaperSpotExitFill | None
    closed_basket: ClosedBasket | None
    closed_basket_count: int
    basket_id: UUID | None
    basket_entry_count: int
    take_profit_price: Decimal | None
    # Read against the last candle the Session accepted, so it answers for the
    # same moment the Session used to decide entries. None until one arrives.
    entry_gate: EntryGate | None

    @property
    def sold_entry_numbers(self) -> tuple[int, ...]:
        """The Entries this snapshot took out of the Basket."""
        return tuple(item.closed.entry_number for item in self.entry_exits)


@dataclass(frozen=True, slots=True)
class PaperSpotSessionIdentity:
    session_id: UUID
    symbol: str
    timeframe: str
    preset_version: str


@dataclass(slots=True)
class _PaperSpotTransition:
    indicators: WilderIndicators
    strategy: EntryStrategy
    lifecycle: EntryPairLifecycle
    basket: Basket | None
    pending_intent: EntryIntent | None
    closed_basket_count: int
    exited_candle_open_time: datetime | None
    entry_number_filled: int | None = None
    entry_take_profit_filled: Decimal | None = None


class PaperSpotSession:
    def __init__(
        self,
        session: SessionConfig,
        market_data: MarketDataConfig,
        symbol_rules: SymbolRules,
        preset: StrategyPreset,
        open_basket: OpenBasketState | None = None,
    ) -> None:
        if (
            session.trade_mode is not TradeMode.PAPER
            or session.market_type is not MarketType.SPOT
        ):
            raise ValueError("PaperSpotSession requires a Paper Spot configuration")
        if session.preset_version != preset.version:
            raise ValueError("session preset version does not match the preset")

        self._session = session
        self._identity = PaperSpotSessionIdentity(
            session_id=session.session_id,
            symbol=market_data.symbol,
            timeframe=market_data.timeframe,
            preset_version=session.preset_version,
        )
        self._symbol_rules = symbol_rules
        self._preset = preset
        self._candles = CompletedCandleStream(market_data)
        self._executor = PaperSpotExecutor(session, symbol_rules)
        self._indicators = WilderIndicators(preset)
        self._strategy = strategy_definition_for(preset.version).build_strategy(
            session.session_id, preset
        )
        self._lifecycle = EntryPairLifecycle(session.entry_policy)
        self._basket: Basket | None = None
        self._pending_intent: EntryIntent | None = None
        self._closed_basket_count = 0
        self._exited_candle_open_time: datetime | None = None
        self._state = PaperSpotSessionState.ACTIVE
        self._failure_reason: PaperSpotFailureReason | None = None
        if open_basket is not None:
            self._reopen_basket(open_basket)

    def _reopen_basket(self, open_basket: OpenBasketState) -> None:
        """Take back a Basket a previous Runtime left open."""
        reopened = reopen_basket(
            open_basket,
            entry_policy=self._session.entry_policy,
            take_profit_atr_multiplier=self._preset.take_profit_atr_multiplier,
            stop_loss_atr_multiplier=self._preset.stop_loss_atr_multiplier,
            tick_size=self._symbol_rules.tick_size,
        )
        self._basket = reopened.basket
        self._lifecycle = reopened.lifecycle
        self._closed_basket_count = open_basket.closed_basket_count

    @property
    def identity(self) -> PaperSpotSessionIdentity:
        return self._identity

    @property
    def open_entries(self) -> tuple[BasketEntry, ...]:
        """The Entries the Basket holds now, each with the target it is sold at."""
        return () if self._basket is None else self._basket.entries

    @property
    def snapshot(self) -> PaperSpotSessionSnapshot:
        return self._snapshot(accepted=self._state is PaperSpotSessionState.ACTIVE)

    def process_completed_candle(
        self, candle: Candle, *, received_at: datetime
    ) -> PaperSpotSessionSnapshot:
        if self._state is not PaperSpotSessionState.ACTIVE:
            return self._snapshot(accepted=False)
        if self._candles.accept(candle, received_at) is not CandleAcceptance.ACCEPTED:
            return self._snapshot(accepted=False)

        try:
            transition = self._new_transition()
            # A Basket this candle already sold between ticks owns the candle:
            # its Entry would fill at an open the exit has moved past.
            sold_between_ticks = transition.exited_candle_open_time == candle.open_time
            basket_existed_at_candle_open = transition.basket is not None
            entry_fill: PaperSpotEntryFill | None = None
            # Read before the Fill clears the Intent that carries it.
            pending_atr = (
                None
                if transition.pending_intent is None
                else transition.pending_intent.atr
            )
            if sold_between_ticks:
                self._reject_pending_intent(transition)
            else:
                entry_fill = self._fill_pending_intent(transition, candle)
            entry_filled_on_current_candle = entry_fill is not None
            entry_number = (
                None if entry_fill is None else transition.entry_number_filled
            )
            entry_target = (
                None if entry_fill is None else transition.entry_take_profit_filled
            )
            exit_fill: PaperSpotExitFill | None = None
            entry_exits: tuple[PaperSpotEntryExit, ...] = ()
            closed_basket: ClosedBasket | None = None

            if basket_existed_at_candle_open and not entry_filled_on_current_candle:
                stop_loss_fill = self._fill_stop_loss(
                    transition, candle, candle.close_time
                )
                if stop_loss_fill is not None:
                    exit_fill = stop_loss_fill
                    closed_basket = self._close_basket(
                        transition, candle, exit_fill, BasketCloseReason.STOP_LOSS
                    )
                else:
                    entry_exits, closed_basket = self._take_profit_entries(
                        transition, candle, candle.close_time
                    )

            indicators = transition.indicators.update(candle)
            self._evaluate_strategy(
                transition,
                candle,
                indicators,
                exited_on_this_candle=(
                    exit_fill is not None or bool(entry_exits) or sold_between_ticks
                ),
            )
            self._commit_transition(transition)

            return self._snapshot(
                accepted=True,
                entry_fill=entry_fill,
                entry_atr=None if entry_fill is None else pending_atr,
                entry_number=entry_number,
                entry_take_profit_price=entry_target,
                entry_exits=entry_exits,
                exit_fill=exit_fill,
                closed_basket=closed_basket,
            )
        except PaperSpotSessionError:
            raise
        except Exception as error:
            self._fail_closed()
            raise PaperSpotSessionError("Paper Spot execution failed") from error

    def process_in_progress_candle(
        self, candle: Candle, *, observed_at: datetime
    ) -> PaperSpotSessionSnapshot:
        """Close an open Basket the moment the forming candle reaches its exit.

        Entries still wait for a completed candle — a signal read from a candle
        that is still moving would be read again as it moves. An exit is not a
        reading: the price is at the level now, and a real exchange would have
        filled it, so waiting for the close would report a fill the market
        already moved past.
        """
        if self._state is not PaperSpotSessionState.ACTIVE or self._basket is None:
            return self._snapshot(accepted=False)
        if not self._is_forming_candle(candle):
            return self._snapshot(accepted=False)

        try:
            transition = self._new_transition()
            exit_fill = self._fill_stop_loss(transition, candle, observed_at)
            if exit_fill is not None:
                stopped_basket = self._close_basket(
                    transition, candle, exit_fill, BasketCloseReason.STOP_LOSS
                )
                self._commit_transition(transition)
                return self._snapshot(
                    accepted=True,
                    exit_fill=exit_fill,
                    closed_basket=stopped_basket,
                )

            entry_exits, closed_basket = self._take_profit_entries(
                transition, candle, observed_at
            )
            if not entry_exits:
                return self._snapshot(accepted=False)

            self._commit_transition(transition)
            return self._snapshot(
                accepted=True,
                entry_exits=entry_exits,
                closed_basket=closed_basket,
            )
        except PaperSpotSessionError:
            raise
        except Exception as error:
            self._fail_closed()
            raise PaperSpotSessionError("Paper Spot execution failed") from error

    def _is_forming_candle(self, candle: Candle) -> bool:
        """Only the candle that opened after the last completed one is forming."""
        closed_at = self._candles.last_close_time
        return (
            candle.symbol == self._identity.symbol
            and candle.timeframe == self._identity.timeframe
            and closed_at is not None
            and candle.open_time == closed_at
        )

    def warm_up_completed_candles(
        self,
        candles: Iterable[Candle],
        *,
        received_at: datetime,
    ) -> None:
        if self._state is not PaperSpotSessionState.ACTIVE:
            raise PaperSpotSessionError("Paper Spot session is not active")
        for candle in candles:
            if (
                self._candles.accept(candle, received_at)
                is not CandleAcceptance.ACCEPTED
            ):
                raise ValueError("warm-up requires new completed candles")
            self._indicators.update(candle)

    def _new_transition(self) -> _PaperSpotTransition:
        return _PaperSpotTransition(
            indicators=deepcopy(self._indicators),
            strategy=deepcopy(self._strategy),
            lifecycle=deepcopy(self._lifecycle),
            basket=deepcopy(self._basket),
            pending_intent=self._pending_intent,
            closed_basket_count=self._closed_basket_count,
            exited_candle_open_time=self._exited_candle_open_time,
        )

    def _commit_transition(self, transition: _PaperSpotTransition) -> None:
        self._indicators = transition.indicators
        self._strategy = transition.strategy
        self._lifecycle = transition.lifecycle
        self._basket = transition.basket
        self._pending_intent = transition.pending_intent
        self._closed_basket_count = transition.closed_basket_count
        self._exited_candle_open_time = transition.exited_candle_open_time

    def _reject_pending_intent(self, transition: _PaperSpotTransition) -> None:
        """Give the Intent back to the strategy, which signals again when it can.

        Holding it instead would fill it at a later open than the candle it was
        read from, pricing an Entry off a signal the market has left behind.
        """
        if transition.pending_intent is None:
            return
        transition.strategy.on_entry_rejected(transition.pending_intent.intent_id)
        transition.pending_intent = None

    def _fill_pending_intent(
        self,
        transition: _PaperSpotTransition,
        candle: Candle,
    ) -> PaperSpotEntryFill | None:
        if transition.pending_intent is None:
            return None

        intent = transition.pending_intent
        fill = self._executor.fill_entry(intent, candle)
        if fill is None:
            transition.strategy.on_entry_rejected(intent.intent_id)
            transition.pending_intent = None
            return None

        if transition.basket is None:
            basket_id = uuid5(
                self._session.session_id,
                f"basket:{transition.closed_basket_count + 1}",
            )
            transition.basket = Basket(
                basket_id,
                self._session.entry_policy,
                self._preset.take_profit_atr_multiplier,
                stop_loss_atr_multiplier=self._preset.stop_loss_atr_multiplier,
            )
        transition.basket.add_entry(
            price=fill.price,
            quantity=fill.quantity,
            filled_at=fill.filled_at,
            atr=intent.atr,
            tick_size=self._symbol_rules.tick_size,
        )
        transition.lifecycle.record_fill(fill.filled_at)
        filled_entry = transition.basket.entries[-1]
        transition.entry_number_filled = filled_entry.entry_number
        transition.entry_take_profit_filled = filled_entry.take_profit_price
        transition.strategy.on_entry_filled(intent.intent_id)
        transition.pending_intent = None
        return fill

    def _fail_closed(self) -> None:
        self._state = PaperSpotSessionState.FAILED_CLOSED
        self._failure_reason = PaperSpotFailureReason.EXECUTION_ERROR
        self._pending_intent = None
        self._strategy = strategy_definition_for(self._preset.version).build_strategy(
            self._session.session_id, self._preset
        )

    def _fill_stop_loss(
        self,
        transition: _PaperSpotTransition,
        candle: Candle,
        filled_at: datetime,
    ) -> PaperSpotExitFill | None:
        assert transition.basket is not None
        return self._executor.fill_stop_loss(
            transition.basket, candle, filled_at=filled_at
        )

    def _take_profit_entries(
        self,
        transition: _PaperSpotTransition,
        candle: Candle,
        filled_at: datetime,
    ) -> tuple[tuple[PaperSpotEntryExit, ...], ClosedBasket | None]:
        """Sell every Entry this candle reached the target of.

        Each sale hands its place in the Basket back, so an Entry that took
        profit frees the quota and the Entry Pair count it was using. The
        Basket itself closes only when its last Entry has gone.
        """
        basket = transition.basket
        assert basket is not None
        fills = self._executor.fill_take_profit(basket, candle, filled_at=filled_at)
        if not fills:
            return (), None

        exits: list[PaperSpotEntryExit] = []
        for fill in fills:
            assert fill.entry_number is not None
            closed_entry = basket.close_entry(
                fill.entry_number,
                exit_price=fill.price,
                    closed_at=fill.filled_at,
            )
            transition.lifecycle.release_fill()
            exits.append(PaperSpotEntryExit(fill=fill, closed=closed_entry))

        # An Entry sold on this candle owns it: an Entry filling at the open
        # would predate a sale the market has already made.
        transition.exited_candle_open_time = candle.open_time
        closed_basket: ClosedBasket | None = None
        if basket.is_empty:
            closed_basket = basket.settled_result(
                exit_price=fills[-1].price,
                closed_at=filled_at,
            )
            transition.basket = None
            transition.lifecycle.reset()
            transition.closed_basket_count += 1
        return tuple(exits), closed_basket

    def _close_basket(
        self,
        transition: _PaperSpotTransition,
        candle: Candle,
        exit_fill: PaperSpotExitFill,
        close_reason: BasketCloseReason,
    ) -> ClosedBasket:
        assert transition.basket is not None
        closed = transition.basket.close(
            exit_price=exit_fill.price,
            closed_at=exit_fill.filled_at,
            close_reason=close_reason,
        )
        transition.exited_candle_open_time = candle.open_time
        transition.basket = None
        transition.lifecycle.reset()
        transition.closed_basket_count += 1
        return closed

    def _evaluate_strategy(
        self,
        transition: _PaperSpotTransition,
        candle: Candle,
        indicators: IndicatorSnapshot | None,
        *,
        exited_on_this_candle: bool = False,
    ) -> None:
        if indicators is None:
            return
        # A signal read from the candle that closed the basket would fill at
        # the very next open, re-entering on the back of its own exit. The
        # earliest a new entry can be signalled is the candle after the sell.
        can_enter = (
            not exited_on_this_candle
            and transition.lifecycle.can_enter(candle.close_time)
            and (
                transition.basket is None
                or transition.basket.entry_count
                < self._session.entry_policy.max_entries
            )
        )
        # Entry numbers count every Entry the Basket has ever taken, so one
        # that sold does not hand its number to the Entry that replaces it.
        entry_sequence = (
            0 if transition.basket is None else transition.basket.entry_sequence
        )
        intent = transition.strategy.evaluate(
            candle,
            indicators,
            entry_number=entry_sequence + 1,
            can_enter=can_enter,
        )
        if intent is not None:
            transition.pending_intent = intent

    def _entry_gate(self) -> EntryGate | None:
        """Read the gate against the last candle, the clock entries are judged on."""
        closed_at = self._candles.last_close_time
        return None if closed_at is None else self._lifecycle.entry_gate(closed_at)

    def _snapshot(
        self,
        *,
        accepted: bool,
        entry_fill: PaperSpotEntryFill | None = None,
        entry_atr: Decimal | None = None,
        entry_number: int | None = None,
        entry_take_profit_price: Decimal | None = None,
        entry_exits: tuple[PaperSpotEntryExit, ...] = (),
        exit_fill: PaperSpotExitFill | None = None,
        closed_basket: ClosedBasket | None = None,
    ) -> PaperSpotSessionSnapshot:
        if self._basket is not None:
            basket_id = self._basket.basket_id
        elif closed_basket is not None:
            basket_id = closed_basket.basket_id
        else:
            basket_id = None
        return PaperSpotSessionSnapshot(
            accepted=accepted,
            state=self._state,
            failure_reason=self._failure_reason,
            pending_intent=self._pending_intent,
            entry_fill=entry_fill,
            entry_atr=entry_atr,
            entry_number=entry_number,
            entry_take_profit_price=entry_take_profit_price,
            entry_exits=entry_exits,
            exit_fill=exit_fill,
            closed_basket=closed_basket,
            closed_basket_count=self._closed_basket_count,
            basket_id=basket_id,
            basket_entry_count=0 if self._basket is None else self._basket.entry_count,
            take_profit_price=(
                None if self._basket is None else self._basket.take_profit_price
            ),
            entry_gate=self._entry_gate(),
        )
