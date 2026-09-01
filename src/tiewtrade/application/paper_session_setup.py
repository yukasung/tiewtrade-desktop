from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from tiewtrade.application.paper_symbols import validate_paper_symbol
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.strategies.registry import (
    StrategyDefinition,
    strategy_definition_for,
    strategy_definitions_for,
)
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode


@dataclass(frozen=True, slots=True)
class PresetChoice:
    preset_version: str
    display_name: str


def preset_choices(market_type: MarketType) -> tuple[PresetChoice, ...]:
    return tuple(
        PresetChoice(definition.preset_version, definition.display_name)
        for definition in strategy_definitions_for(market_type)
    )


def preset_display_name(preset_version: str) -> str:
    try:
        return strategy_definition_for(preset_version).display_name
    except ValueError:
        return preset_version


@dataclass(frozen=True, slots=True)
class PaperSessionSetupValues:
    market_type: str
    symbol: str
    timeframe: str
    capital_per_entry: str
    max_entries: str
    futures_leverage: str | None
    preset_version: str = "rsi-step-grid-v1"


@dataclass(frozen=True, slots=True)
class ConfiguredPaperSession:
    config: SessionConfig
    market_data: MarketDataConfig
    created_at_utc: datetime
    ended_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if (
            self.created_at_utc.tzinfo is None
            or self.created_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("created_at_utc must use UTC")
        if self.ended_at_utc is not None:
            raise ValueError("new Paper Session must be active")


def setup_values_of(session: ConfiguredPaperSession) -> PaperSessionSetupValues:
    """The values a form shows for a Session it was filled from.

    Written once so that "the form still describes this Session" is a
    comparison rather than a guess: the widgets are filled from here, and
    what comes back out of them is read against the same strings. A second
    formatter would let a trailing zero read as an edit nobody made.
    """
    config = session.config
    futures_policy = config.futures_policy
    return PaperSessionSetupValues(
        market_type=config.market_type.value,
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
        capital_per_entry=format(config.capital_per_entry, "f"),
        max_entries=str(config.entry_policy.max_entries),
        futures_leverage=(
            None if futures_policy is None else str(futures_policy.leverage)
        ),
        preset_version=config.preset_version,
    )


@dataclass(frozen=True, slots=True)
class PaperSessionCreateOutcome:
    session: ConfiguredPaperSession
    created: bool


class PaperSessionValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class PaperSessionUnavailableError(RuntimeError):
    pass


class ActivePaperSessionStillRunningError(PaperSessionUnavailableError):
    """Something is still running the Session that was asked to be retired."""


class ActivePaperSessionGoneError(PaperSessionUnavailableError):
    """There is no Active Session left to retire — the row it had has gone."""


CreateActiveSession = Callable[[ConfiguredPaperSession], PaperSessionCreateOutcome]


class CreatePaperSession:
    def __init__(
        self,
        *,
        create_active: CreateActiveSession,
        session_ids: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._create_active = create_active
        self._session_ids = session_ids
        self._clock = clock

    def execute(self, values: PaperSessionSetupValues) -> PaperSessionCreateOutcome:
        setup = _parse(values)
        config = _validate_field(
            "capital_per_entry",
            lambda: SessionConfig(
                session_id=self._session_ids(),
                preset_version=setup.preset.version,
                market_type=setup.market_type,
                trade_mode=TradeMode.PAPER,
                capital_per_entry=setup.capital_per_entry,
                entry_policy=setup.entry_policy,
                futures_policy=setup.futures_policy,
            ),
        )
        session = ConfiguredPaperSession(
            config=config,
            market_data=setup.market_data,
            created_at_utc=self._clock(),
        )
        return self._create_active(session)


def validate_paper_session(values: PaperSessionSetupValues) -> None:
    """Whether a form could start a Session, asked without starting one.

    The form asks this on its own so it can mark the field at fault before a
    Session is written. It is the same reading `CreatePaperSession` does, not
    a second one kept in step by hand: a rule added here is a rule the use
    case enforces, and one it enforces cannot pass this.
    """
    _parse(values)


@dataclass(frozen=True, slots=True)
class _ParsedSetup:
    """A form read into the values a Session is built from."""

    market_type: MarketType
    capital_per_entry: Decimal
    entry_policy: EntryPolicy
    futures_policy: FuturesTradingPolicy | None
    market_data: MarketDataConfig
    preset: StrategyPreset


def _parse(values: PaperSessionSetupValues) -> _ParsedSetup:
    """Read a form, reporting the first field that cannot be read.

    The order fields are read in is the order they are reported in, so a form
    with more than one thing wrong marks the same field every time.
    """
    market_type = _market_type(values.market_type)
    capital_per_entry = _decimal(values.capital_per_entry, "capital_per_entry")
    max_entries = _integer(values.max_entries, "max_entries")
    try:
        validate_paper_symbol(values.symbol)
    except ValueError as error:
        raise PaperSessionValidationError("symbol", str(error)) from error
    if capital_per_entry <= 0:
        raise PaperSessionValidationError(
            "capital_per_entry", "Amount must be positive"
        )

    futures_policy: FuturesTradingPolicy | None = None
    if market_type is MarketType.FUTURES:
        leverage = _integer(
            _required(values.futures_leverage, "futures_leverage"),
            "futures_leverage",
        )
        futures_policy = _validate_field(
            "futures_leverage", lambda: FuturesTradingPolicy.v1(leverage)
        )

    entry_policy = _validate_field("max_entries", lambda: EntryPolicy(max_entries))
    market_data = _validate_field(
        "timeframe",
        lambda: MarketDataConfig(symbol=values.symbol, timeframe=values.timeframe),
    )
    preset = _validate_field(
        "preset_version",
        lambda: _strategy_definition_for_market_type(
            values.preset_version, market_type
        ).build_preset(),
    )
    return _ParsedSetup(
        market_type=market_type,
        capital_per_entry=capital_per_entry,
        entry_policy=entry_policy,
        futures_policy=futures_policy,
        market_data=market_data,
        preset=preset,
    )


def _strategy_definition_for_market_type(
    preset_version: str, market_type: MarketType
) -> StrategyDefinition:
    definition = strategy_definition_for(preset_version)
    if market_type not in definition.market_types:
        raise ValueError(
            f"strategy {preset_version!r} is not available for {market_type.value}"
        )
    return definition


def _required(value: str | None, field: str) -> str:
    if value is None or not value.strip():
        raise PaperSessionValidationError(field, "This field is required")
    return value


def _decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(_required(value, field))
    except InvalidOperation as error:
        raise PaperSessionValidationError(field, "Enter a valid number") from error
    if not parsed.is_finite():
        raise PaperSessionValidationError(field, "Enter a finite number")
    return parsed


def _integer(value: str, field: str) -> int:
    try:
        return int(_required(value, field))
    except ValueError as error:
        raise PaperSessionValidationError(field, "Enter a whole number") from error


def _market_type(value: str) -> MarketType:
    try:
        return MarketType(value)
    except ValueError as error:
        raise PaperSessionValidationError(
            "market_type", "Select Spot or Futures"
        ) from error


def _validate_field[Parsed](field: str, factory: Callable[[], Parsed]) -> Parsed:
    try:
        return factory()
    except ValueError as error:
        raise PaperSessionValidationError(field, str(error)) from error
