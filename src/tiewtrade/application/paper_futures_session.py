from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.application.paper_candle_transition import PaperCandleTransition
from tiewtrade.execution.paper_futures import (
    PaperFuturesEntryFill,
    PaperFuturesExecutor,
    PaperFuturesExitFill,
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
    BasketPair,
    ClosedBasket,
    ClosedEntry,
)
from tiewtrade.trading.capital import FuturesCapitalPlan
from tiewtrade.trading.entry_pair import EntryGate, EntryPairLifecycle
from tiewtrade.trading.position import PositionSide, account_equity
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules


class PaperFuturesSessionState(StrEnum):
    ACTIVE = "active"
    FAILED_CLOSED = "failed_closed"


class PaperFuturesFailureReason(StrEnum):
    EXECUTION_ERROR = "execution_error"


class PaperFuturesSessionError(RuntimeError):
    """Paper Futures stopped because an execution invariant failed."""


@dataclass(frozen=True, slots=True)
class PaperFuturesSessionIdentity:
    session_id: UUID
    symbol: str
    timeframe: str
    preset_version: str
    leverage: int


@dataclass(frozen=True, slots=True)
class PaperFuturesEntryExit:
    """One Entry a target took out of the Basket, and what it left behind."""

    fill: PaperFuturesExitFill
    closed: ClosedEntry


@dataclass(frozen=True, slots=True)
class PaperFuturesSessionSnapshot:
    accepted: bool
    state: PaperFuturesSessionState
    pending_intent: EntryIntent | None
    entry_fill: PaperFuturesEntryFill | None
    # The number of the Entry just filled, which the tab reads per Entry.
    entry_number: int | None
    # Entries a target sold this candle, each at the price its own rule gave
    # it. Futures has no other way out — no stop loss, and no sale the trader
    # asks for — so every exit this Session books arrives here.
    entry_exits: tuple[PaperFuturesEntryExit, ...]
    closed_basket: ClosedBasket | None
    basket_id: UUID | None
    basket_entry_count: int
    position_side: PositionSide | None
    take_profit_price: Decimal | None
    # What each open Entry Pair paid and leaves at; see the Spot snapshot.
    entry_pairs: tuple[BasketPair, ...]
    account_equity: Decimal
    capital_plan: FuturesCapitalPlan
    failure_reason: PaperFuturesFailureReason | None
    # Read against the last candle the Session accepted, so it answers for the
    # same moment the Session used to decide entries. None until one arrives.
    entry_gate: EntryGate | None
    # Entry Pairs the Basket has completed across its whole life, never back —
    # the same count that decides which Entry a standing Cooldown blocks.
    entry_completed_pairs: int

    @property
    def sold_entry_numbers(self) -> tuple[int, ...]:
        """The Entries this snapshot took out of the Basket."""
        return tuple(item.closed.entry_number for item in self.entry_exits)

    @property
    def failed_closed(self) -> bool:
        """This Session has stopped for good, whatever it was asked next."""
        return self.state is PaperFuturesSessionState.FAILED_CLOSED


@dataclass(slots=True, kw_only=True)
class _PaperFuturesTransition(PaperCandleTransition):
    """What one candle would leave this Session holding, if it gets that far.

    The wallet is Futures' own: nothing else a candle moves is money already
    banked, and Spot has no such balance to carry.
    """

    wallet_balance: Decimal


class PaperFuturesSession:
    def __init__(
        self,
        session: SessionConfig,
        market_data: MarketDataConfig,
        symbol_rules: SymbolRules,
        preset: StrategyPreset,
    ) -> None:
        if (
            session.trade_mode is not TradeMode.PAPER
            or session.market_type is not MarketType.FUTURES
        ):
            raise ValueError(
                "PaperFuturesSession requires a Paper Futures configuration"
            )
        futures_policy = session.futures_policy
        if futures_policy is None:
            raise ValueError("PaperFuturesSession requires futures_policy")
        if session.preset_version != preset.version:
            raise ValueError("session preset version does not match the preset")

        self._identity = PaperFuturesSessionIdentity(
            session_id=session.session_id,
            symbol=market_data.symbol,
            timeframe=market_data.timeframe,
            preset_version=session.preset_version,
            leverage=futures_policy.leverage,
        )
        self._session = session
        self._symbol_rules = symbol_rules
        self._preset = preset
        self._candles = CompletedCandleStream(market_data)
        self._executor = PaperFuturesExecutor(session, symbol_rules)
        self._capital_plan = FuturesCapitalPlan.from_capital_per_entry(
            session.capital_per_entry,
            futures_policy,
        )
        self._indicators = WilderIndicators(preset)
        self._strategy = strategy_definition_for(preset.version).build_strategy(
            session.session_id, preset
        )
        self._lifecycle = EntryPairLifecycle(session.entry_policy)
        self._state = PaperFuturesSessionState.ACTIVE
        self._failure_reason: PaperFuturesFailureReason | None = None
        self._basket: Basket | None = None
        self._pending_intent: EntryIntent | None = None
        self._wallet_balance = session.session_capital
        self._account_equity = session.session_capital
        self._latest_mark_price: Decimal | None = None
        self._closed_basket_count = 0

    @property
    def identity(self) -> PaperFuturesSessionIdentity:
        return self._identity

    @property
    def snapshot(self) -> PaperFuturesSessionSnapshot:
        return self._snapshot(accepted=self._state is PaperFuturesSessionState.ACTIVE)

    def process_completed_candle(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool = False,
    ) -> PaperFuturesSessionSnapshot:
        """Act on one completed candle, live or recovered after a disconnect.

        A recovered candle counts for everything except opening an Entry: it
        feeds the indicators, and it can still take profit, because that
        happened in the market whether the feed saw it or not. Only buying at
        its open is refused, that price being gone.
        """
        if self._state is not PaperFuturesSessionState.ACTIVE:
            return self._snapshot(accepted=False)
        if self._candles.accept(candle, received_at) is not CandleAcceptance.ACCEPTED:
            return self._snapshot(accepted=False)

        self._latest_mark_price = candle.close
        basket_existed_at_open = self._basket is not None
        try:
            transition = self._new_transition()
            # An Intent handed back can be signalled again on a live candle;
            # one left pending would fill there off a signal read long before.
            if backfilled:
                transition.reject_pending_intent()
                entry_fill = None
            else:
                entry_fill = self._fill_pending_intent(transition, candle)
            entry_exits: tuple[PaperFuturesEntryExit, ...] = ()
            closed: ClosedBasket | None = None
            if basket_existed_at_open and entry_fill is None:
                entry_exits, closed = self._take_profit_entries(transition, candle)

            indicators = transition.indicators.update(candle)
            transition.evaluate_strategy(
                candle,
                indicators,
                max_entries=self._session.entry_policy.max_entries,
                exited_on_this_candle=bool(entry_exits),
            )
            self._commit_transition(transition)
            self._mark_open_basket()
            return self._snapshot(
                accepted=True,
                entry_fill=entry_fill,
                entry_exits=entry_exits,
                closed_basket=closed,
            )
        except PaperFuturesSessionError:
            raise
        except Exception as error:
            self._fail_closed()
            raise PaperFuturesSessionError("Paper Futures execution failed") from error

    def process_in_progress_candle(
        self, candle: Candle, *, observed_at: datetime
    ) -> PaperFuturesSessionSnapshot:
        """Paper Futures does not act between candles, and reports nothing here.

        Selling the moment a forming candle touches the target claims a price
        the candle may never close near, which reads as a win the market did
        not hand over. A completed candle is a price that stood.
        """
        del candle, observed_at
        return self._snapshot(accepted=False)

    def warm_up_completed_candles(
        self,
        candles: Iterable[Candle],
        *,
        received_at: datetime,
    ) -> None:
        if self._state is not PaperFuturesSessionState.ACTIVE:
            raise PaperFuturesSessionError("Paper Futures session is not active")
        for candle in candles:
            require_warm_up_candle(self._candles, candle, received_at)
            self._indicators.update(candle)

    def waive_cooldown(self, observed_at: datetime) -> PaperFuturesSessionSnapshot:
        """Let the trader open past the Cooldown blocking this Basket now.

        Futures never takes a Basket back from the database, so a Cooldown it
        is holding is lifted here or not at all.

        An unaccepted snapshot is the answer where there is nothing to waive —
        no Basket, or a gate that is not a Cooldown right now.
        """
        if self._state is not PaperFuturesSessionState.ACTIVE or self._basket is None:
            return self._snapshot(accepted=False)
        transition = self._new_transition()
        try:
            transition.lifecycle.waive_cooldown()
        except ValueError:
            return self._snapshot(accepted=False)
        self._commit_transition(transition)
        return self._snapshot(accepted=True)

    def _new_transition(self) -> _PaperFuturesTransition:
        return _PaperFuturesTransition(
            indicators=deepcopy(self._indicators),
            strategy=deepcopy(self._strategy),
            lifecycle=deepcopy(self._lifecycle),
            basket=deepcopy(self._basket),
            pending_intent=self._pending_intent,
            closed_basket_count=self._closed_basket_count,
            wallet_balance=self._wallet_balance,
        )

    def _commit_transition(self, transition: _PaperFuturesTransition) -> None:
        self._indicators = transition.indicators
        self._strategy = transition.strategy
        self._lifecycle = transition.lifecycle
        self._basket = transition.basket
        self._pending_intent = transition.pending_intent
        self._closed_basket_count = transition.closed_basket_count
        self._wallet_balance = transition.wallet_balance

    def _fill_pending_intent(
        self, transition: _PaperFuturesTransition, candle: Candle
    ) -> PaperFuturesEntryFill | None:
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
                position_side=fill.side,
            )
        transition.basket.add_entry(
            price=fill.price,
            quantity=fill.quantity,
            filled_at=fill.filled_at,
            atr=intent.atr,
            tick_size=self._symbol_rules.tick_size,
            position_side=fill.side,
        )
        transition.accept_pending_intent()
        return fill

    def _take_profit_entries(
        self, transition: _PaperFuturesTransition, candle: Candle
    ) -> tuple[tuple[PaperFuturesEntryExit, ...], ClosedBasket | None]:
        """Sell every Entry this candle carried to a target.

        Each sale hands its place in the Basket back, so an Entry that took
        profit frees the quota and the Entry Pair count it was using, and what
        it realised reaches the wallet as it goes rather than waiting for the
        round to end. The Basket itself closes only when its last Entry has
        gone — which a candle reaching the Basket's own target always does,
        since nothing is left behind it.
        """
        basket = transition.basket
        assert basket is not None
        fills = self._executor.fill_take_profit(basket, candle)
        if not fills:
            return (), None

        exits: list[PaperFuturesEntryExit] = []
        for fill in fills:
            closed_entry = transition.sell_entry(
                fill.entry_number,
                exit_price=fill.price,
                closed_at=fill.filled_at,
            )
            # Every Entry books its own result, so the wallet grows by the sum
            # of them rather than by a Basket figure counting the same money
            # again.
            transition.wallet_balance += closed_entry.realized_pnl
            exits.append(PaperFuturesEntryExit(fill=fill, closed=closed_entry))

        closed_basket = transition.settle_emptied_basket(
            exit_price=fills[-1].price,
            closed_at=fills[-1].filled_at,
            close_reason=BasketCloseReason.TAKE_PROFIT,
        )
        return tuple(exits), closed_basket

    def _mark_open_basket(self) -> None:
        if self._basket is None:
            self._account_equity = self._wallet_balance
            return
        assert self._latest_mark_price is not None
        self._account_equity = account_equity(
            side=self._basket.position_side,
            average_entry_price=self._basket.average_entry_price,
            quantity=self._basket.total_quantity,
            available_capital=self._wallet_balance,
            current_price=self._latest_mark_price,
        )

    def _fail_closed(self) -> None:
        self._state = PaperFuturesSessionState.FAILED_CLOSED
        self._failure_reason = PaperFuturesFailureReason.EXECUTION_ERROR
        self._pending_intent = None
        self._strategy = strategy_definition_for(self._preset.version).build_strategy(
            self._session.session_id, self._preset
        )

    def _entry_gate(self) -> EntryGate | None:
        """The gate, once the Session has seen a candle to state one against.

        The gate itself no longer turns on the clock, but a Session that has
        watched no market yet is not holding a Basket to answer for either.
        """
        if self._candles.last_close_time is None:
            return None
        return self._lifecycle.entry_gate()

    def _snapshot(
        self,
        *,
        accepted: bool,
        entry_fill: PaperFuturesEntryFill | None = None,
        entry_exits: tuple[PaperFuturesEntryExit, ...] = (),
        closed_basket: ClosedBasket | None = None,
    ) -> PaperFuturesSessionSnapshot:
        basket_id: UUID | None
        position_side: PositionSide | None
        if self._basket is not None:
            basket_id = self._basket.basket_id
            basket_entry_count = self._basket.entry_count
            position_side = self._basket.position_side
            take_profit_price = self._basket.take_profit_price
        else:
            basket_id = None if closed_basket is None else closed_basket.basket_id
            basket_entry_count = 0
            position_side = (
                None if closed_basket is None else closed_basket.position_side
            )
            take_profit_price = None

        # The Entry just filled is the newest the Basket holds.
        filled_entry = (
            self._basket.entries[-1]
            if entry_fill is not None and self._basket is not None
            else None
        )
        return PaperFuturesSessionSnapshot(
            accepted=accepted,
            state=self._state,
            pending_intent=self._pending_intent,
            entry_fill=entry_fill,
            entry_number=None if filled_entry is None else filled_entry.entry_number,
            entry_exits=entry_exits,
            closed_basket=closed_basket,
            basket_id=basket_id,
            basket_entry_count=basket_entry_count,
            position_side=position_side,
            take_profit_price=take_profit_price,
            entry_pairs=(
                () if self._basket is None else self._basket.open_pair_views()
            ),
            account_equity=self._account_equity,
            capital_plan=self._capital_plan,
            failure_reason=self._failure_reason,
            entry_gate=self._entry_gate(),
            entry_completed_pairs=self._lifecycle.completed_pairs,
        )
