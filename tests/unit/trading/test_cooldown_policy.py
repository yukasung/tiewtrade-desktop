from datetime import UTC, datetime, timedelta

import pytest

from tiewtrade.trading.cooldown_policy import CooldownMode, CooldownPolicy

COMPLETED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def test_until_cancelled_carries_no_duration() -> None:
    policy = CooldownPolicy.until_cancelled()

    assert policy.mode is CooldownMode.UNTIL_CANCELLED
    assert policy.days is None


def test_off_carries_no_duration() -> None:
    policy = CooldownPolicy.off()

    assert policy.mode is CooldownMode.OFF
    assert policy.days is None


def test_for_days_carries_the_duration_it_was_given() -> None:
    policy = CooldownPolicy.for_days(7)

    assert policy.mode is CooldownMode.DURATION
    assert policy.days == 7


def test_exposes_supported_day_bounds() -> None:
    assert CooldownPolicy.MINIMUM_DAYS == 1
    assert CooldownPolicy.MAXIMUM_DAYS == 30
    assert CooldownPolicy.for_days(CooldownPolicy.MINIMUM_DAYS).days == 1
    assert CooldownPolicy.for_days(CooldownPolicy.MAXIMUM_DAYS).days == 30


@pytest.mark.parametrize(
    "days",
    [
        CooldownPolicy.MINIMUM_DAYS - 1,
        CooldownPolicy.MAXIMUM_DAYS + 1,
        1.5,
        True,
    ],
)
def test_rejects_a_duration_outside_the_supported_days(days: object) -> None:
    with pytest.raises(ValueError, match="days"):
        CooldownPolicy.for_days(days)  # type: ignore[arg-type]


def test_rejects_a_duration_mode_without_days() -> None:
    with pytest.raises(ValueError, match="days"):
        CooldownPolicy(mode=CooldownMode.DURATION)


@pytest.mark.parametrize("mode", [CooldownMode.OFF, CooldownMode.UNTIL_CANCELLED])
def test_rejects_days_on_a_mode_that_does_not_wait(mode: CooldownMode) -> None:
    with pytest.raises(ValueError, match="days"):
        CooldownPolicy(mode=mode, days=7)


@pytest.mark.parametrize(
    "policy",
    [CooldownPolicy.off(), CooldownPolicy.until_cancelled()],
)
def test_only_a_duration_ever_lapses(policy: CooldownPolicy) -> None:
    assert not policy.has_lapsed(
        completed_at=COMPLETED_AT, as_of=COMPLETED_AT + timedelta(days=3650)
    )


def test_a_duration_lapses_on_the_day_it_names_and_not_before() -> None:
    policy = CooldownPolicy.for_days(3)
    deadline = COMPLETED_AT + timedelta(days=3)

    assert not policy.has_lapsed(completed_at=COMPLETED_AT, as_of=COMPLETED_AT)
    assert not policy.has_lapsed(
        completed_at=COMPLETED_AT, as_of=deadline - timedelta(seconds=1)
    )
    assert policy.has_lapsed(completed_at=COMPLETED_AT, as_of=deadline)
    assert policy.has_lapsed(
        completed_at=COMPLETED_AT, as_of=deadline + timedelta(days=1)
    )


def test_a_duration_names_when_it_lifts() -> None:
    policy = CooldownPolicy.for_days(3)

    assert policy.ends_at(completed_at=COMPLETED_AT) == COMPLETED_AT + timedelta(days=3)


@pytest.mark.parametrize(
    "policy",
    [CooldownPolicy.off(), CooldownPolicy.until_cancelled()],
)
def test_a_cooldown_that_does_not_lapse_names_no_end(policy: CooldownPolicy) -> None:
    assert policy.ends_at(completed_at=COMPLETED_AT) is None
