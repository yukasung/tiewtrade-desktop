from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.trading.entry_pair import entry_pair_index
from tiewtrade.trading.session_config import MarketType, TradeMode


class WorkspaceReadState(StrEnum):
    LOADING = "loading"
    EMPTY = "empty"
    READY = "ready"
    ERROR = "error"
    STALE = "stale"


class WorkspaceTabState(StrEnum):
    LOADING = "loading"
    EMPTY = "empty"
    READY = "ready"
    ERROR = "error"
    STALE = "stale"


class BotRuntimeState(StrEnum):
    NO_SESSION = "no_session"
    CONFIGURED = "configured"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    BLOCKED = "blocked"


class BasketEntryGate(StrEnum):
    """Why a Basket may or may not take another Entry, as the workspace reads it.

    A view-facing echo of the domain's EntryGateState, member for member: the
    UI is kept out of the Entry Pair rules, so the gate crosses this layer as
    plain facts. Producers restate a domain gate as BasketEntryGate(state).
    """

    OPEN = "open"
    COOLDOWN = "cooldown"
    MAX_ENTRIES_REACHED = "max_entries_reached"


class DataFreshness(StrEnum):
    NOT_STARTED = "not_started"
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


POSITION_TAB_UNAVAILABLE_MESSAGE = "Position data is unavailable"
# An open Position that cannot be put back the way it was left. Saying "no
# Position" instead would be the one answer that is not true, so the tab says
# what is wrong and what clears it.
POSITION_BASKET_UNREADABLE_MESSAGE = (
    "This Session has an open Position the Bot cannot take back. "
    "Press Start Paper Bot and answer Yes to close it."
)
_SAFE_POSITION_BASKET_TAB_MESSAGES = frozenset(
    {
        POSITION_TAB_UNAVAILABLE_MESSAGE,
        POSITION_BASKET_UNREADABLE_MESSAGE,
    }
)


@dataclass(frozen=True, slots=True)
class WorkspaceHeaderSnapshot:
    symbol: str
    timeframe: str
    trade_mode: TradeMode
    market_type: MarketType
    preset_version: str
    runtime_state: BotRuntimeState
    data_freshness: DataFreshness

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.timeframe, "timeframe")
        _require_text(self.preset_version, "preset_version")
        if not isinstance(self.trade_mode, TradeMode):
            raise ValueError("trade_mode must be a TradeMode")
        if not isinstance(self.market_type, MarketType):
            raise ValueError("market_type must be a MarketType")
        if not isinstance(self.runtime_state, BotRuntimeState):
            raise ValueError("runtime_state must be a BotRuntimeState")
        if not isinstance(self.data_freshness, DataFreshness):
            raise ValueError("data_freshness must be a DataFreshness")


@dataclass(frozen=True, slots=True)
class BasketEntrySnapshot:
    """One Entry of a Basket, as the trader placed it.

    A Basket averages its Entries away, so the average alone cannot say which
    price was bought when. Each Entry carries its own reading, already marked
    against the Basket's current price, so the view never recalculates.

    What it is worth is the one reading that needs a price, and a Basket read
    out of the database is known before any price has arrived for it. No
    reading yet is None rather than zero: zero is a figure, and it would read
    as an Entry standing exactly where it was bought.
    """

    entry_number: int
    filled_at_utc: datetime
    price: Decimal
    quantity: Decimal
    notional: Decimal
    unrealized_pnl: Decimal | None
    # The target this Entry alone is sold at, measured from the price it paid.
    take_profit_price: Decimal

    def __post_init__(self) -> None:
        if type(self.entry_number) is not int or self.entry_number < 1:
            raise ValueError("entry_number must be a positive integer")
        _require_utc(self.filled_at_utc, "filled_at_utc")
        _require_decimal(self.take_profit_price, "take_profit_price")
        _require_decimal(self.price, "price")
        _require_non_negative_decimal(self.quantity, "quantity")
        _require_decimal(self.notional, "notional")
        _require_decimal(self.unrealized_pnl, "unrealized_pnl", allow_none=True)


@dataclass(frozen=True, slots=True)
class BasketSnapshot:
    symbol: str
    market_type: str
    entry_count: int
    entries: tuple[BasketEntrySnapshot, ...]
    total_quantity: Decimal
    average_entry_price: Decimal
    # None until a price arrives to mark this Basket on, which is how a
    # Basket read back out of the database stands before its first candle.
    current_price: Decimal | None
    take_profit_price: Decimal
    unrealized_pnl: Decimal | None
    entry_gate: BasketEntryGate
    updated_at_utc: datetime
    # Entry Pairs this Basket has completed across its whole life, counted
    # forward and never back — the same count `EntryPairLifecycle` keeps. A
    # row reads its own gate against this: an Entry from a Pair before the
    # one just completed is not what a standing Cooldown is blocking. Left at
    # its default, every row falls back to reading the Basket's own gate.
    completed_pairs: int = 0

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.market_type, "market_type")
        if type(self.entry_count) is not int or self.entry_count < 0:
            raise ValueError("entry_count must be a non-negative integer")
        _require_basket_entries(self.entries, self.entry_count)
        _require_non_negative_decimal(self.total_quantity, "total_quantity")
        _require_decimal(self.average_entry_price, "average_entry_price")
        _require_decimal(self.current_price, "current_price", allow_none=True)
        _require_decimal(self.take_profit_price, "take_profit_price")
        _require_decimal(self.unrealized_pnl, "unrealized_pnl", allow_none=True)
        _require_one_marking(self.current_price, self.unrealized_pnl, self.entries)
        if not isinstance(self.entry_gate, BasketEntryGate):
            raise ValueError("entry_gate must be a BasketEntryGate")
        if type(self.completed_pairs) is not int or self.completed_pairs < 0:
            raise ValueError("completed_pairs must be a non-negative integer")
        _require_utc(self.updated_at_utc, "updated_at_utc")

    def entry_gate_for(self, entry_number: int) -> BasketEntryGate:
        """The gate as it stands for one Entry, not the Basket read as a whole.

        A Cooldown blocks the next Entry, not the ones an earlier,
        already-resolved Pair left open — those were waived past their own
        Cooldown long before this one started, or they would not be here for a
        later Pair to have formed. So an Entry from a Pair before the one that
        just completed reads Open even while the Basket itself is cooling down.

        The same reading `EntryPairLifecycle.entry_gate_for` makes of the live
        lifecycle, made here of what a screen was handed — which is why the
        Pair an Entry belongs to is counted by the rule's own module rather
        than again here.
        """
        if self.entry_gate is not BasketEntryGate.COOLDOWN:
            return self.entry_gate
        if entry_pair_index(entry_number) < self.completed_pairs:
            return BasketEntryGate.OPEN
        return self.entry_gate


@dataclass(frozen=True, slots=True)
class PositionBasketTabSnapshot:
    state: WorkspaceTabState
    basket: BasketSnapshot | None
    data_as_of_utc: datetime | None
    message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, WorkspaceTabState):
            raise ValueError("state must be a WorkspaceTabState")
        if self.basket is not None and not isinstance(self.basket, BasketSnapshot):
            raise ValueError("basket must be a BasketSnapshot")
        if self.data_as_of_utc is not None:
            _require_utc(self.data_as_of_utc, "data_as_of_utc")
        if self.message is not None:
            _require_text(self.message, "message")
        self._validate_state_combination()

    def _validate_state_combination(self) -> None:
        if self.state is WorkspaceTabState.EMPTY:
            if self.basket is not None:
                raise ValueError("EMPTY tab must not contain durable data")
            if self.message is not None:
                raise ValueError("EMPTY tab must not contain a message")
        elif self.state is WorkspaceTabState.READY:
            if self.basket is None:
                raise ValueError("READY tab requires durable data")
            if self.data_as_of_utc is None:
                raise ValueError("READY tab requires data_as_of_utc")
            if self.message is not None:
                raise ValueError("READY tab must not contain a message")
        elif self.state is WorkspaceTabState.LOADING:
            if self.message is not None:
                raise ValueError("LOADING tab must not contain a message")
        elif self.state is WorkspaceTabState.ERROR:
            if self.message is None:
                raise ValueError("ERROR tab requires a message")
            _require_sanitized_position_basket_message(self.message)
        elif self.state is WorkspaceTabState.STALE:
            if self.data_as_of_utc is None:
                raise ValueError("STALE tab requires data_as_of_utc")
            if self.message is not None:
                raise ValueError("STALE tab must not contain a message")


@dataclass(frozen=True, slots=True)
class TradingWorkspaceSnapshot:
    read_state: WorkspaceReadState
    header: WorkspaceHeaderSnapshot | None
    position_basket: PositionBasketTabSnapshot
    data_as_of_utc: datetime | None
    message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.read_state, WorkspaceReadState):
            raise ValueError("read_state must be a WorkspaceReadState")
        if self.header is not None and not isinstance(
            self.header, WorkspaceHeaderSnapshot
        ):
            raise ValueError("header must be a WorkspaceHeaderSnapshot")
        if not isinstance(self.position_basket, PositionBasketTabSnapshot):
            raise ValueError("position_basket must be a PositionBasketTabSnapshot")
        if self.data_as_of_utc is not None:
            _require_utc(self.data_as_of_utc, "data_as_of_utc")
        if self.message is not None:
            _require_text(self.message, "message")
        self._validate_state_combination()

    @property
    def basket(self) -> BasketSnapshot | None:
        return self.position_basket.basket

    def _validate_state_combination(self) -> None:
        if self.read_state is WorkspaceReadState.EMPTY:
            if self.header is not None or self.basket is not None:
                raise ValueError("EMPTY snapshot must not contain durable data")
            if self.message is not None:
                raise ValueError("EMPTY snapshot must not contain a message")
        elif self.read_state is WorkspaceReadState.READY:
            if self.header is None:
                raise ValueError("READY snapshot requires a header")
            if self.data_as_of_utc is None:
                raise ValueError("READY snapshot requires data_as_of_utc")
            if self.header.data_freshness is DataFreshness.STALE:
                raise ValueError("READY snapshot must not have stale data")
            if self.message is not None:
                raise ValueError("READY snapshot must not contain a message")
        elif self.read_state is WorkspaceReadState.ERROR:
            if self.message is None:
                raise ValueError("ERROR snapshot requires a message")
        elif self.read_state is WorkspaceReadState.STALE:
            if self.header is None:
                raise ValueError("STALE snapshot requires a header")
            if self.data_as_of_utc is None:
                raise ValueError("STALE snapshot requires data_as_of_utc")
            if self.header.data_freshness is not DataFreshness.STALE:
                raise ValueError("STALE snapshot requires stale data freshness")
            if self.message is not None:
                raise ValueError("STALE snapshot must not contain a message")
        elif self.message is not None:
            raise ValueError("LOADING snapshot must not contain a message")


def empty_workspace_snapshot(
    *, observed_at_utc: datetime | None = None
) -> TradingWorkspaceSnapshot:
    return TradingWorkspaceSnapshot(
        read_state=WorkspaceReadState.EMPTY,
        header=None,
        position_basket=empty_position_basket_tab(),
        data_as_of_utc=observed_at_utc,
    )


def configured_workspace_snapshot(
    session: ConfiguredPaperSession, *, observed_at_utc: datetime
) -> TradingWorkspaceSnapshot:
    return TradingWorkspaceSnapshot(
        read_state=WorkspaceReadState.READY,
        header=WorkspaceHeaderSnapshot(
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            trade_mode=session.config.trade_mode,
            market_type=session.config.market_type,
            preset_version=session.config.preset_version,
            runtime_state=BotRuntimeState.CONFIGURED,
            data_freshness=DataFreshness.NOT_STARTED,
        ),
        position_basket=empty_position_basket_tab(),
        data_as_of_utc=observed_at_utc,
    )


def loading_workspace_snapshot(
    last_known: TradingWorkspaceSnapshot,
) -> TradingWorkspaceSnapshot:
    return replace(last_known, read_state=WorkspaceReadState.LOADING, message=None)


def failed_workspace_snapshot(
    last_known: TradingWorkspaceSnapshot, message: str
) -> TradingWorkspaceSnapshot:
    return replace(last_known, read_state=WorkspaceReadState.ERROR, message=message)


def stale_workspace_snapshot(
    last_known: TradingWorkspaceSnapshot,
) -> TradingWorkspaceSnapshot:
    if last_known.header is None:
        raise ValueError("STALE snapshot requires a header")
    return replace(
        last_known,
        read_state=WorkspaceReadState.STALE,
        header=replace(last_known.header, data_freshness=DataFreshness.STALE),
        message=None,
    )


def paper_runtime_workspace_snapshot(
    last_known: TradingWorkspaceSnapshot,
    *,
    basket: BasketSnapshot | None,
    observed_at_utc: datetime,
) -> TradingWorkspaceSnapshot:
    if last_known.header is None:
        raise ValueError("Paper Runtime workspace requires a header")
    _require_utc(observed_at_utc, "observed_at_utc")
    position_basket = (
        PositionBasketTabSnapshot(
            state=WorkspaceTabState.EMPTY,
            basket=None,
            data_as_of_utc=observed_at_utc,
        )
        if basket is None
        else ready_position_basket_tab(basket, observed_at_utc=observed_at_utc)
    )
    return TradingWorkspaceSnapshot(
        read_state=WorkspaceReadState.READY,
        header=replace(
            last_known.header,
            runtime_state=BotRuntimeState.RUNNING,
            data_freshness=DataFreshness.FRESH,
        ),
        position_basket=position_basket,
        data_as_of_utc=observed_at_utc,
    )


def paper_runtime_blocked_workspace_snapshot(
    last_known: TradingWorkspaceSnapshot,
) -> TradingWorkspaceSnapshot:
    if last_known.header is None:
        raise ValueError("Paper Runtime workspace requires a header")
    if last_known.data_as_of_utc is None:
        raise ValueError("Paper Runtime workspace requires data_as_of_utc")
    return replace(
        last_known,
        read_state=WorkspaceReadState.STALE,
        header=replace(
            last_known.header,
            runtime_state=BotRuntimeState.BLOCKED,
            data_freshness=DataFreshness.STALE,
        ),
        message=None,
    )


def empty_position_basket_tab() -> PositionBasketTabSnapshot:
    return PositionBasketTabSnapshot(
        state=WorkspaceTabState.EMPTY,
        basket=None,
        data_as_of_utc=None,
    )


def ready_position_basket_tab(
    basket: BasketSnapshot, *, observed_at_utc: datetime
) -> PositionBasketTabSnapshot:
    _require_utc(observed_at_utc, "observed_at_utc")
    return PositionBasketTabSnapshot(
        state=WorkspaceTabState.READY,
        basket=basket,
        data_as_of_utc=observed_at_utc,
    )


def loading_position_basket_tab(
    last_known: PositionBasketTabSnapshot,
) -> PositionBasketTabSnapshot:
    _require_position_basket_tab(last_known, "last_known")
    return replace(last_known, state=WorkspaceTabState.LOADING, message=None)


def failed_position_basket_tab(
    last_known: PositionBasketTabSnapshot, message: str
) -> PositionBasketTabSnapshot:
    _require_position_basket_tab(last_known, "last_known")
    return replace(last_known, state=WorkspaceTabState.ERROR, message=message)


def stale_position_basket_tab(
    last_known: PositionBasketTabSnapshot,
) -> PositionBasketTabSnapshot:
    _require_position_basket_tab(last_known, "last_known")
    if last_known.data_as_of_utc is None:
        raise ValueError("STALE tab requires data_as_of_utc")
    return replace(last_known, state=WorkspaceTabState.STALE, message=None)


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_utc(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must use UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must use UTC")


def _require_decimal(
    value: Decimal | None, name: str, *, allow_none: bool = False
) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, Decimal):
        raise ValueError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _require_non_negative_decimal(value: Decimal, name: str) -> None:
    _require_decimal(value, name)
    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_basket_entries(
    value: tuple[BasketEntrySnapshot, ...], entry_count: int
) -> None:
    """One Entry per counted Entry, numbered in the order they were taken.

    Counting Entries one way and listing them another would let the table and
    its total disagree about the same Basket.

    The numbers themselves are an audit trail: an Entry sold at its own target
    leaves the Basket without handing its number back, so what is left over
    can be Entry 2 alone. Demanding 1..entry_count read that ordinary Basket
    as impossible and took the whole Position tab down with it. What still
    holds is that no two rows claim one number and that they are listed in the
    order they were taken.
    """
    if not isinstance(value, tuple) or not all(
        isinstance(entry, BasketEntrySnapshot) for entry in value
    ):
        raise ValueError("entries must be a tuple of BasketEntrySnapshot")
    if len(value) != entry_count:
        raise ValueError("entries must hold one Entry per entry_count")
    numbers = tuple(entry.entry_number for entry in value)
    if numbers != tuple(sorted(set(numbers))):
        raise ValueError("entries must be numbered without repeats, in order")


def _require_one_marking(
    current_price: Decimal | None,
    unrealized_pnl: Decimal | None,
    entries: tuple[BasketEntrySnapshot, ...],
) -> None:
    """A Basket is marked throughout or nowhere, never half of each.

    Every figure that needs a price is read off the same price, so a Basket
    with none of its own cannot carry a result that was somehow reached
    without one. Half a marking is a reading no price could produce.
    """
    marked = current_price is not None
    if marked != (unrealized_pnl is not None):
        raise ValueError("unrealized_pnl belongs to a marked Basket and only one")
    if any((entry.unrealized_pnl is not None) != marked for entry in entries):
        raise ValueError("every Entry is marked with the Basket, or none is")


def _require_position_basket_tab(value: object, name: str) -> None:
    if not isinstance(value, PositionBasketTabSnapshot):
        raise ValueError(f"{name} must be a PositionBasketTabSnapshot")


def _require_sanitized_position_basket_message(message: str) -> None:
    if message not in _SAFE_POSITION_BASKET_TAB_MESSAGES:
        raise ValueError("message must be sanitized")
