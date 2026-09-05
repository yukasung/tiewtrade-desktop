"""Every lifecycle a Basket can actually reach, for rules that hold in all of them.

A rule about Entry Pairs is easy to state for the Basket in front of you and
easy to break three sales later — the case that broke it was a Pair completed
by a Fill whose partner had already sold, which no single example test thought
to write down. Walking the states a Basket can reach, one action at a time, is
how a rule gets asked about the cases nobody thought of.

The walk is the ordinary life of a Basket and nothing else: it takes a Fill
only where the gate allows one, sells only Entries that are open, and waives
only a Cooldown that is standing. Each state comes with what a restart would
read of it, so the rule can be asked of the live lifecycle and of replay and
the two answers compared.

Every action lands on its own candle, so the walk carries market time the way
a Session does: Entry n filled on candle n, and the gate is asked as of the
candle the next action would land on.
"""

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tiewtrade.trading.entry_pair import (
    EntryGateState,
    EntryPairLifecycle,
    entry_pair_index,
)
from tiewtrade.trading.entry_policy import EntryPolicy

WALK_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
WALK_CANDLE = timedelta(minutes=5)


def walk_time(step: int) -> datetime:
    """The market time of the nth candle of the walk, counting from one."""
    return WALK_EPOCH + step * WALK_CANDLE


@dataclass(frozen=True, slots=True)
class ReachedLifecycle:
    """A lifecycle a Basket reached, beside what the database would hold of it."""

    lifecycle: EntryPairLifecycle
    open_entry_numbers: tuple[int, ...]
    settled_entry_count: int
    #: The waiver storage keeps, which is the only fact no Fill carries.
    waived_pair: int | None
    #: The candle this Basket stands on, which is what its gate answers as of.
    as_of: datetime

    def replayed(self, policy: EntryPolicy) -> EntryPairLifecycle:
        """The same Basket as a restart would put it back together."""
        return EntryPairLifecycle.replayed(
            policy,
            open_entries=tuple(
                (number, walk_time(number)) for number in self.open_entry_numbers
            ),
            settled_entry_count=self.settled_entry_count,
            waived_pair=self.waived_pair,
        )

    def cooling_pairs(self) -> set[int]:
        """The Pairs that read as cooling down across the Entries still open."""
        return {
            entry_pair_index(number)
            for number in self.open_entry_numbers
            if self.lifecycle.entry_gate_for(number, self.as_of).state
            is EntryGateState.COOLDOWN
        }


def reachable_lifecycles(
    policy: EntryPolicy,
    *,
    actions: int = 8,
) -> Iterator[ReachedLifecycle]:
    """Walk every Basket reachable in `actions` fills, sales and waivers.

    Entry numbers are the Basket's own: the nth Fill is Entry n, and a sold
    Entry never hands its number back, which is what lets replay read a Pair
    off the Entries that are left.
    """

    def walk(
        lifecycle: EntryPairLifecycle,
        open_entries: frozenset[int],
        next_number: int,
        waived_pair: int | None,
        left: int,
    ) -> Iterator[ReachedLifecycle]:
        as_of = walk_time(next_number)
        yield ReachedLifecycle(
            lifecycle=lifecycle,
            open_entry_numbers=tuple(sorted(open_entries)),
            settled_entry_count=next_number - 1 - len(open_entries),
            waived_pair=waived_pair,
            as_of=as_of,
        )
        if left == 0:
            return
        if lifecycle.can_enter(as_of):
            filled = deepcopy(lifecycle)
            filled.record_fill(as_of)
            yield from walk(
                filled,
                open_entries | {next_number},
                next_number + 1,
                waived_pair,
                left - 1,
            )
        for number in sorted(open_entries):
            sold = deepcopy(lifecycle)
            sold.release_fill(number)
            yield from walk(
                sold, open_entries - {number}, next_number, waived_pair, left - 1
            )
        if lifecycle.entry_gate(as_of).state is EntryGateState.COOLDOWN:
            waived = deepcopy(lifecycle)
            waived.waive_cooldown(as_of)
            yield from walk(
                waived, open_entries, next_number, waived.completed_pairs, left - 1
            )

    yield from walk(EntryPairLifecycle(policy), frozenset(), 1, None, actions)
