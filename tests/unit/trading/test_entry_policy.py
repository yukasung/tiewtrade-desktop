import pytest

from tiewtrade.trading.cooldown_policy import CooldownPolicy
from tiewtrade.trading.entry_policy import EntryPolicy


def test_a_policy_stated_by_entries_alone_waits_the_longest_cooldown() -> None:
    """A Session assembled without a choice holds a Basket back the most."""
    policy = EntryPolicy(max_entries=4)

    assert policy.max_entries == 4
    assert policy.cooldown == CooldownPolicy.default()


def test_a_chosen_cooldown_is_carried_alongside_the_entry_count() -> None:
    policy = EntryPolicy(max_entries=4, cooldown=CooldownPolicy.for_days(3))

    assert policy.cooldown == CooldownPolicy.for_days(3)


@pytest.mark.parametrize("max_entries", [0, 1, 3, 21, 22])
def test_rejects_an_entry_count_outside_an_even_two_to_twenty(
    max_entries: int,
) -> None:
    with pytest.raises(ValueError, match="max_entries"):
        EntryPolicy(max_entries=max_entries)
