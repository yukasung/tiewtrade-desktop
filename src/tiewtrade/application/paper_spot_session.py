from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.application.open_basket import OpenBasketState, reopen_basket
from tiewtrade.application.paper_candle_transition import PaperCandleTransition
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
from tiewtrade.market_data.warm_up import require_warm_up_candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.indicators import WilderIndicators
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.basket import (
    Basket,
    BasketCloseReason,
    BasketEntry,
    BasketEntryTarget,
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
    """One Entry the trader sold on its own, and what it left behind."""

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
    # The Entry Group this Entry bought into, and where that Group's target
    # stood once it had. Present exactly when entry_fill is: a Group's target
    # only ever moves closer, so where it stopped is history rather than a
    # reading, and only a record of every move it made brings it back.
    entry_group_id: int | None
    entry_group_take_profit_price: Decimal | None
    # Entries the trader sold one at a time, rather than with the Basket.
    entry_exits: tuple[PaperSpotEntryExit, ...]
    # A Fill that took the whole position at once, which take profit, a stop
    # loss and a manual close of the Position all do.
    exit_fill: PaperSpotExitFill | None
    closed_basket: ClosedBasket | None
    closed_basket_count: int
    basket_id: UUID | None
    basket_entry_count: int
    take_profit_price: Decimal | None
    # What each open Entry paid, the Group it is in, and what that Group
    # leaves at. A row on screen reads its Group's target off this rather than
    # the Basket-wide one, which is only the nearest of the Groups.
    entry_targets: tuple[BasketEntryTarget, ...]
    # Read against the last candle the Session accepted, so it answers for the
    # same moment the Session used to decide entries. None until one arrives.
    entry_gate: EntryGate | None
    # The Pair a Cooldown waiver just accepted, numbered the way the lifecycle
    # numbers it. Present exactly when a waiver was accepted.
    waived_pair: int | None
    waived_at: datetime | None
    # Entry Pairs the Basket has completed across its whole life, never back —
    # the same count that decides which Entry a standing Cooldown blocks.
    entry_completed_pairs: int
    # When the Cooldown standing over this Basket lifts on its own. None
    # where no Cooldown is standing, and under a Session whose Cooldown is off.
    cooldown_ends_at: datetime | None

    @property
    def sold_entry_numbers(self) -> tuple[int, ...]:
        """The Entries this snapshot took out of the Basket."""
        return tuple(item.closed.entry_number for item in self.entry_exits)

    @property
    def failed_closed(self) -> bool:
        """This Session has stopped for good, whatever it was asked next."""
        return self.state is PaperSpotSessionState.FAILED_CLOSED


@dataclass(frozen=True, slots=True)
class PaperSpotSessionIdentity:
    session_id: UUID
    symbol: str
    timeframe: str
    preset_version: str


@dataclass(slots=True, kw_only=True)
class _PaperSpotTransition(PaperCandleTransition):
    """What one candle would leave this Session holding, if it gets that far.

    Both fields are Spot's own. An exit between ticks is something only Spot
    takes, so only Spot has to remember which candle one owns; and only Spot
    reports the number of the Entry a Fill opened, which the tab reads per row.
    """

    exited_candle_open_time: datetime | None
    entry_number_filled: int | None = None


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
        """The Entries the Basket holds now, each as it was filled."""
        return () if self._basket is None else self._basket.entries

    @property
    def snapshot(self) -> PaperSpotSessionSnapshot:
        return self._snapshot(accepted=self._state is PaperSpotSessionState.ACTIVE)

    def process_completed_candle(
        self, candle: Candle, *, received_at: datetime, backfilled: bool = False
    ) -> PaperSpotSessionSnapshot:
        """Act on one completed candle, live or recovered after a disconnect.

        A recovered candle counts for everything except opening an Entry: it
        feeds the indicators, it can reach a target and close a Basket, but
        it cannot buy, because its open is a price the market has left.
        """
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
            # An Intent handed back can be signalled again on a live candle;
            # one left pending would fill there off a signal read long before.
            if sold_between_ticks or backfilled:
                transition.reject_pending_intent()
            else:
                entry_fill = self._fill_pending_intent(transition, candle)
            entry_filled_on_current_candle = entry_fill is not None
            entry_number = (
                None if entry_fill is None else transition.entry_number_filled
            )
            exit_fill: PaperSpotExitFill | None = None
            entry_exits: tuple[PaperSpotEntryExit, ...] = ()
            closed_basket: ClosedBasket | None = None

            if basket_existed_at_candle_open and not entry_filled_on_current_candle:
                entry_exits, closed_basket = self._take_profit_entries(
                    transition, candle, candle.close_time
                )

            indicators = transition.indicators.update(candle)
            transition.evaluate_strategy(
                candle,
                indicators,
                max_entries=self._session.entry_policy.max_entries,
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
            require_warm_up_candle(self._candles, candle, received_at)
            self._indicators.update(candle)

    def close_entry(
        self,
        entry_number: int,
        *,
        price: Decimal,
        observed_at: datetime,
    ) -> PaperSpotSessionSnapshot:
        """Sell one Entry now, at the price the trader asked for it at.

        The same sale a target makes, and made the same way: the Entry hands
        its place in the Basket back, so the quota and the Entry Pair count it
        was using are free again, and the Basket closes only when its last
        Entry has gone. What differs is who asked and at what price.

        A Cooldown does not stand in the way of this. It says the Basket is
        holding both Entries of a Pair at once, and this sale is what stops
        that being true — refusing it left the only Cooldown a trader could
        not resolve by trading their way out of it.

        An unaccepted snapshot is the answer where there is nothing to sell —
        no Basket, or no Entry by that number — so a stale row on screen
        cannot sell an Entry the Session has already let go.
        """
        if self._state is not PaperSpotSessionState.ACTIVE or self._basket is None:
            return self._snapshot(accepted=False)
        if all(entry.entry_number != entry_number for entry in self._basket.entries):
            return self._snapshot(accepted=False)
        try:
            transition = self._new_transition()
            basket = transition.basket
            assert basket is not None
            fill = self._executor.fill_entry_close(
                basket, entry_number, price=price, filled_at=observed_at
            )
            closed_entry = transition.sell_entry(
                entry_number,
                exit_price=fill.price,
                closed_at=fill.filled_at,
            )
            closed_basket = transition.settle_emptied_basket(
                exit_price=fill.price,
                closed_at=observed_at,
                close_reason=BasketCloseReason.MANUAL,
            )
            self._commit_transition(transition)
            return self._snapshot(
                accepted=True,
                entry_exits=(PaperSpotEntryExit(fill=fill, closed=closed_entry),),
                closed_basket=closed_basket,
            )
        except PaperSpotSessionError:
            raise
        except Exception as error:
            self._fail_closed()
            raise PaperSpotSessionError("Paper Spot execution failed") from error

    def waive_cooldown(self, observed_at: datetime) -> PaperSpotSessionSnapshot:
        """Let the trader open past the Cooldown blocking this Basket now.

        An unaccepted snapshot is the answer where there is nothing to waive —
        no Basket, or a gate that is not a Cooldown right now.
        """
        if self._state is not PaperSpotSessionState.ACTIVE or self._basket is None:
            return self._snapshot(accepted=False)
        try:
            transition = self._new_transition()
            transition.lifecycle.waive_cooldown(observed_at)
            waived_pair = transition.lifecycle.completed_pairs
            self._commit_transition(transition)
            return self._snapshot(
                accepted=True,
                waived_pair=waived_pair,
                waived_at=observed_at,
            )
        except ValueError:
            return self._snapshot(accepted=False)
        except PaperSpotSessionError:
            raise
        except Exception as error:
            self._fail_closed()
            raise PaperSpotSessionError("Paper Spot execution failed") from error

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
            transition.reject_pending_intent()
            return None

        if transition.basket is None:
            transition.basket = Basket(
                transition.next_basket_id(self._session.session_id),
                self._session.entry_policy,
                self._preset.take_profit_atr_multiplier,
            )
        transition.basket.add_entry(
            price=fill.price,
            quantity=fill.quantity,
            filled_at=fill.filled_at,
            atr=intent.atr,
            tick_size=self._symbol_rules.tick_size,
        )
        transition.entry_number_filled = transition.basket.entries[-1].entry_number
        transition.accept_pending_intent(fill.filled_at)
        return fill

    def _fail_closed(self) -> None:
        self._state = PaperSpotSessionState.FAILED_CLOSED
        self._failure_reason = PaperSpotFailureReason.EXECUTION_ERROR
        self._pending_intent = None
        self._strategy = strategy_definition_for(self._preset.version).build_strategy(
            self._session.session_id, self._preset
        )

    def _take_profit_entries(
        self,
        transition: _PaperSpotTransition,
        candle: Candle,
        filled_at: datetime,
    ) -> tuple[tuple[PaperSpotEntryExit, ...], ClosedBasket | None]:
        """Sell every Entry this candle carried to a target.

        Each sale hands its place in the Basket back, so an Entry that took
        profit frees the quota and the Entry Pair count it was using. The
        Basket itself closes only when its last Entry has gone — which a candle
        reaching the Basket's own target always does, since nothing is left
        behind it.
        """
        basket = transition.basket
        assert basket is not None
        fills = self._executor.fill_take_profit(basket, candle, filled_at=filled_at)
        if not fills:
            return (), None

        exits: list[PaperSpotEntryExit] = []
        for fill in fills:
            assert fill.entry_number is not None
            closed_entry = transition.sell_entry(
                fill.entry_number,
                exit_price=fill.price,
                closed_at=fill.filled_at,
            )
            exits.append(PaperSpotEntryExit(fill=fill, closed=closed_entry))

        # An Entry sold on this candle owns it: an Entry filling at the open
        # would predate a sale the market has already made.
        transition.exited_candle_open_time = candle.open_time
        closed_basket = transition.settle_emptied_basket(
            exit_price=fills[-1].price,
            closed_at=filled_at,
            close_reason=BasketCloseReason.TAKE_PROFIT,
        )
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
        transition.retire_basket()
        return closed

    def _entry_gate(self) -> EntryGate | None:
        """The gate, once the Session has seen a candle to state one against.

        The candle the Session last saw is also the clock the gate is asked
        against — market time, never the machine's, so a Cooldown that waits
        answers the same way every time these candles are replayed. A Session
        that has watched no market yet has no time to ask as of, and is not
        holding a Basket to answer for either.
        """
        if self._candles.last_close_time is None:
            return None
        return self._lifecycle.entry_gate(self._candles.last_close_time)

    def _snapshot(
        self,
        *,
        accepted: bool,
        entry_fill: PaperSpotEntryFill | None = None,
        entry_atr: Decimal | None = None,
        entry_number: int | None = None,
        entry_exits: tuple[PaperSpotEntryExit, ...] = (),
        exit_fill: PaperSpotExitFill | None = None,
        closed_basket: ClosedBasket | None = None,
        waived_pair: int | None = None,
        waived_at: datetime | None = None,
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
            # Read off the Basket rather than handed in: the Group an Entry
            # joined and the price that Group now waits at are the Basket's
            # answer, and asking it here is what keeps the record and the
            # Position tab reading the same figure.
            entry_group_id=(
                None
                if entry_fill is None or self._basket is None or entry_number is None
                else self._basket.entry_group_id(entry_number)
            ),
            entry_group_take_profit_price=(
                None
                if entry_fill is None or self._basket is None or entry_number is None
                else self._basket.entry_take_profit_price(entry_number)
            ),
            entry_exits=entry_exits,
            exit_fill=exit_fill,
            closed_basket=closed_basket,
            closed_basket_count=self._closed_basket_count,
            basket_id=basket_id,
            basket_entry_count=0 if self._basket is None else self._basket.entry_count,
            take_profit_price=(
                None if self._basket is None else self._basket.take_profit_price
            ),
            entry_targets=(
                () if self._basket is None else self._basket.open_entry_targets()
            ),
            entry_gate=self._entry_gate(),
            waived_pair=waived_pair,
            waived_at=waived_at,
            entry_completed_pairs=self._lifecycle.completed_pairs,
            cooldown_ends_at=self._lifecycle.cooldown_ends_at(),
        )
