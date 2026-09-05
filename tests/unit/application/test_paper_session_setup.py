from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    CreatePaperSession,
    PaperSessionCreateOutcome,
    PaperSessionSetupValues,
    PaperSessionUnavailableError,
    PaperSessionValidationError,
    setup_values_of,
)
from tiewtrade.trading.cooldown_policy import CooldownPolicy
from tiewtrade.trading.session_config import MarketType, TradeMode

SESSION_ID = UUID("00000000-0000-0000-0000-000000000123")
CREATED_AT = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)


def spot_values() -> PaperSessionSetupValues:
    return PaperSessionSetupValues(
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="5m",
        capital_per_entry="200000",
        max_entries="10",
        futures_leverage=None,
    )


def test_create_spot_session_builds_immutable_configuration() -> None:
    recorded: list[ConfiguredPaperSession] = []

    def create_active(session: ConfiguredPaperSession) -> PaperSessionCreateOutcome:
        recorded.append(session)
        return PaperSessionCreateOutcome(session=session, created=True)

    use_case = CreatePaperSession(
        create_active=create_active,
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    outcome = use_case.execute(spot_values())

    assert outcome.created is True
    assert outcome.session.config.session_id == SESSION_ID
    assert outcome.session.config.trade_mode is TradeMode.PAPER
    assert outcome.session.config.market_type is MarketType.SPOT
    assert outcome.session.market_data.symbol == "BTCUSDT"
    assert outcome.session.market_data.timeframe == "5m"
    assert outcome.session.config.entry_policy.max_entries == 10
    assert outcome.session.config.capital_per_entry == Decimal("200000")
    assert outcome.session.config.futures_policy is None
    assert outcome.session.created_at_utc == CREATED_AT
    assert recorded == [outcome.session]


def test_create_paper_session_accepts_ethusdt() -> None:
    use_case = CreatePaperSession(
        create_active=lambda session: PaperSessionCreateOutcome(
            session=session, created=True
        ),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    outcome = use_case.execute(replace(spot_values(), symbol="ETHUSDT"))

    assert outcome.session.market_data.symbol == "ETHUSDT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capital_per_entry", "0"),
        ("max_entries", "3"),
    ],
)
def test_invalid_input_reports_the_exact_field(field: str, value: str) -> None:
    values = replace(spot_values(), **{field: value})
    use_case = CreatePaperSession(
        create_active=lambda session: pytest.fail("must not persist"),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PaperSessionValidationError) as caught:
        use_case.execute(values)

    assert caught.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capital_per_entry", "not-a-number"),
        ("max_entries", "10.5"),
    ],
)
def test_malformed_input_reports_the_exact_field(field: str, value: str) -> None:
    values = replace(spot_values(), **{field: value})
    use_case = CreatePaperSession(
        create_active=lambda session: pytest.fail("must not persist"),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PaperSessionValidationError) as caught:
        use_case.execute(values)

    assert caught.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capital_per_entry", "Infinity"),
        ("capital_per_entry", "NaN"),
    ],
)
def test_non_finite_input_reports_the_exact_field(field: str, value: str) -> None:
    values = replace(spot_values(), **{field: value})
    use_case = CreatePaperSession(
        create_active=lambda session: pytest.fail("must not persist"),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PaperSessionValidationError) as caught:
        use_case.execute(values)

    assert caught.value.field == field


def test_create_futures_session_builds_v1_policy() -> None:
    values = replace(
        spot_values(),
        market_type="futures",
        futures_leverage="3",
    )
    use_case = CreatePaperSession(
        create_active=lambda session: PaperSessionCreateOutcome(session, True),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    outcome = use_case.execute(values)

    policy = outcome.session.config.futures_policy
    assert outcome.session.config.market_type is MarketType.FUTURES
    assert policy is not None
    assert policy.leverage == 3
    assert policy.margin_mode.value == "cross"
    assert policy.position_mode.value == "one_way"


def test_futures_session_requires_leverage() -> None:
    values = replace(
        spot_values(),
        market_type="futures",
        futures_leverage=None,
    )
    use_case = CreatePaperSession(
        create_active=lambda session: pytest.fail("must not persist"),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PaperSessionValidationError) as caught:
        use_case.execute(values)

    assert caught.value.field == "futures_leverage"


def test_persistence_unavailable_error_is_not_reported_as_validation() -> None:
    def unavailable(session: ConfiguredPaperSession) -> PaperSessionCreateOutcome:
        raise PaperSessionUnavailableError("persistence unavailable")

    use_case = CreatePaperSession(
        create_active=unavailable,
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PaperSessionUnavailableError, match="persistence unavailable"):
        use_case.execute(spot_values())


def _configured(values: PaperSessionSetupValues) -> ConfiguredPaperSession:
    use_case = CreatePaperSession(
        create_active=lambda session: PaperSessionCreateOutcome(
            session=session, created=True
        ),
        session_ids=lambda: SESSION_ID,
        clock=lambda: CREATED_AT,
    )
    return use_case.execute(values).session


def test_a_form_that_names_no_cooldown_waits_the_longest_one() -> None:
    """Assembled without a choice, a Session is the most cautious one there is."""
    session = _configured(spot_values())

    assert session.config.entry_policy.cooldown == CooldownPolicy.default()


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (None, CooldownPolicy.off()),
        ("1", CooldownPolicy.for_days(1)),
        ("3", CooldownPolicy.for_days(3)),
        ("30", CooldownPolicy.for_days(30)),
    ],
)
def test_a_form_states_the_cooldown_the_session_is_pinned_to(
    days: str | None, expected: CooldownPolicy
) -> None:
    session = _configured(replace(spot_values(), cooldown_days=days))

    assert session.config.entry_policy.cooldown == expected


@pytest.mark.parametrize("days", ["0", "31", "many"])
def test_days_the_policy_refuses_are_reported_against_the_field_that_asked(
    days: str,
) -> None:
    with pytest.raises(PaperSessionValidationError) as caught:
        _configured(replace(spot_values(), cooldown_days=days))

    assert caught.value.field == "cooldown_days"


@pytest.mark.parametrize(
    "cooldown",
    [CooldownPolicy.off(), CooldownPolicy.for_days(3), CooldownPolicy.default()],
)
def test_a_session_reads_back_onto_the_form_it_was_started_from(
    cooldown: CooldownPolicy,
) -> None:
    """An untouched form compares equal to the Session it was filled from."""
    values = replace(
        spot_values(),
        cooldown_days=None if cooldown.days is None else str(cooldown.days),
    )
    session = _configured(values)

    assert setup_values_of(session) == values
