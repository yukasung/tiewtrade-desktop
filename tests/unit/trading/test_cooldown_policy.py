from datetime import UTC, datetime, timedelta

import pytest

from tiewtrade.trading.cooldown_policy import CooldownPolicy

COMPLETED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def test_a_cooldown_that_is_off_waits_no_days() -> None:
    policy = CooldownPolicy.off()

    assert policy.days is None
    assert policy.never_cools_down


def test_for_days_carries_the_duration_it_was_given() -> None:
    policy = CooldownPolicy.for_days(7)

    assert policy.days == 7
    assert not policy.never_cools_down


def test_a_session_that_names_no_cooldown_waits_the_longest_one() -> None:
    """The default is the most it can hold a Basket back, not the least."""
    assert CooldownPolicy.default() == CooldownPolicy.for_days(
        CooldownPolicy.MAXIMUM_DAYS
    )
    assert CooldownPolicy.DEFAULT_DAYS == 30


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


def test_a_cooldown_that_is_off_never_lapses_because_it_never_starts() -> None:
    policy = CooldownPolicy.off()

    assert not policy.has_lapsed(
        completed_at=COMPLETED_AT, as_of=COMPLETED_AT + timedelta(days=3650)
    )
    assert policy.ends_at(completed_at=COMPLETED_AT) is None


def test_a_cooldown_lapses_on_the_day_it_names_and_not_before() -> None:
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


def test_a_cooldown_names_when_it_lifts() -> None:
    policy = CooldownPolicy.for_days(3)

    assert policy.ends_at(completed_at=COMPLETED_AT) == COMPLETED_AT + timedelta(days=3)
