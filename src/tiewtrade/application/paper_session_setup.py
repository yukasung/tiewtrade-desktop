from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from tiewtrade.application.paper_symbols import validate_paper_symbol
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.registry import (
    StrategyDefinition,
    strategy_definition_for,
    strategy_definitions_for,
)
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.spot_policy import SpotTradingPolicy


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
    available_capital: str
    max_entries: str
    slippage_bps: str
    spot_trading_capital_percent: str | None
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
        validate_paper_session(values)
        market_type = _market_type(values.market_type)
        available_capital = _decimal(values.available_capital, "available_capital")
        max_entries = _integer(values.max_entries, "max_entries")
        slippage_bps = _decimal(values.slippage_bps, "slippage_bps")

        spot_policy: SpotTradingPolicy | None = None
        futures_policy: FuturesTradingPolicy | None = None
        if market_type is MarketType.SPOT:
            spot_policy = spot_trading_policy_from_percent(
                _required(
                    values.spot_trading_capital_percent,
                    "spot_trading_capital_percent",
                )
            )
        else:
            leverage = _integer(
                _required(values.futures_leverage, "futures_leverage"),
                "futures_leverage",
            )
            futures_policy = _validate_field(
                "futures_leverage", lambda: FuturesTradingPolicy.v1(leverage)
            )

        preset = _validate_field(
            "preset_version",
            lambda: _strategy_definition_for_market_type(
                values.preset_version, market_type
            ).build_preset(),
        )
        entry_policy = _validate_field("max_entries", lambda: EntryPolicy(max_entries))
        market_data = _validate_field(
            "timeframe",
            lambda: MarketDataConfig(
                symbol=values.symbol,
                timeframe=values.timeframe,
            ),
        )
        config = _validate_field(
            "available_capital",
            lambda: SessionConfig(
                session_id=self._session_ids(),
                preset_version=preset.version,
                market_type=market_type,
                trade_mode=TradeMode.PAPER,
                available_capital=available_capital,
                slippage_bps=slippage_bps,
                entry_policy=entry_policy,
                spot_policy=spot_policy,
                futures_policy=futures_policy,
            ),
        )
        session = ConfiguredPaperSession(
            config=config,
            market_data=market_data,
            created_at_utc=self._clock(),
        )
        return self._create_active(session)


def validate_paper_session(values: PaperSessionSetupValues) -> None:
    market_type = _market_type(values.market_type)
    available_capital = _decimal(values.available_capital, "available_capital")
    _integer(values.max_entries, "max_entries")
    slippage_bps = _decimal(values.slippage_bps, "slippage_bps")
    try:
        validate_paper_symbol(values.symbol)
    except ValueError as error:
        raise PaperSessionValidationError("symbol", str(error)) from error
    if available_capital <= 0:
        raise PaperSessionValidationError(
            "available_capital", "Available Capital must be positive"
        )
    if not Decimal("0") <= slippage_bps < Decimal("10000"):
        raise PaperSessionValidationError(
            "slippage_bps", "Slippage must be below 10,000 bps"
        )
    if market_type is MarketType.SPOT:
        spot_trading_policy_from_percent(
            _required(
                values.spot_trading_capital_percent,
                "spot_trading_capital_percent",
            )
        )
    else:
        leverage = _integer(
            _required(values.futures_leverage, "futures_leverage"),
            "futures_leverage",
        )
        _validate_field("futures_leverage", lambda: FuturesTradingPolicy.v1(leverage))
    _validate_field(
        "max_entries",
        lambda: EntryPolicy(_integer(values.max_entries, "max_entries")),
    )
    _validate_field(
        "timeframe",
        lambda: MarketDataConfig(symbol=values.symbol, timeframe=values.timeframe),
    )
    _validate_field(
        "preset_version",
        lambda: _strategy_definition_for_market_type(
            values.preset_version, market_type
        ),
    )


def spot_trading_policy_from_percent(value: str) -> SpotTradingPolicy:
    field = "spot_trading_capital_percent"
    ratio = _decimal(value, field) / Decimal("100")
    return _validate_field(field, lambda: SpotTradingPolicy(ratio))


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
