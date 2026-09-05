from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tiewtrade.trading.cooldown_policy import CooldownMode
from tiewtrade.trading.entry_policy import EntryPolicy


class EntryGateState(StrEnum):
    OPEN = "open"
    COOLDOWN = "cooldown"
    MAX_ENTRIES_REACHED = "max_entries_reached"


@dataclass(frozen=True, slots=True)
class EntryGate:
    """Whether an Entry may open now, and which rule closed it.

    A blocked gate carries which of the two rules blocked it, because they
    mean opposite things to a reader: a Cooldown is lifted by the trader,
    while a Session out of Entries never opens again.
    """

    state: EntryGateState

    @property
    def is_open(self) -> bool:
        return self.state is EntryGateState.OPEN


def entry_pair_index(entry_number: int) -> int:
    """Which Entry Pair this Entry belongs to, counted across the Basket's life.

    `entry_number` is a lifetime count of every Entry the Basket has ever
    taken, so two Entries make a Pair the same way `_count_fill` counts one.
    """
    return (entry_number - 1) // 2 + 1


class EntryPairLifecycle:
    def __init__(self, policy: EntryPolicy) -> None:
        self._policy = policy
        self._entry_count = 0
        # Every Fill this Basket has ever taken, counted forward and never
        # back — unlike `_entry_count`, an Entry that later sells does not
        # hand its place back here. Whether a Pair completed is read off this
        # rather than off how many Entries happen to be open right now, so
        # selling an Entry out of order can never unmake or misname a Pair
        # that formed after it.
        self._filled_total = 0
        # Pairs this Basket has completed, counted forward and never back.
        self._completed_pairs = 0
        self._waived_pair: int | None = None
        # Every Entry this Basket has sold, by number. A Cooldown is the
        # Basket holding both Entries of a Pair at once, so what ends one is
        # either of them having gone — whether it went after the Pair formed
        # or before its partner ever filled. Kept as the Entries themselves
        # rather than as "the Pair that was broken up" because that is the
        # same thing replay has to read back off the Entries still open, and
        # a rule stated twice is a rule that drifts.
        self._released_entries: set[int] = set()
        # When the Fill that completed the latest Pair happened, in market
        # time. A Cooldown that waits is measured from here; the other two
        # modes never read it.
        self._pair_completed_at: datetime | None = None

    @classmethod
    def replayed(
        cls,
        policy: EntryPolicy,
        *,
        open_entries: Sequence[tuple[int, datetime]],
        settled_entry_count: int = 0,
        waived_pair: int | None,
    ) -> "EntryPairLifecycle":
        """A lifecycle standing where Fills already recorded left it.

        Replay never asks the gate. The gate decides whether a *new* Entry may
        open, and an Entry the market has already taken is not up for decision
        — asking it re-argued Cooldowns the trader had lifted long ago. Only
        the latest waiver is kept, so the ones before it can never be answered
        for, and a Basket carried past two Cooldowns could not be put back.

        `settled_entry_count` is what this Basket already sold before the
        Runtime that reads it now started — a Fill counts toward a Pair
        whether or not the Entry it opened is still here to show for it, so
        leaving those Fills out would let a Cooldown a restart should still
        answer for read as one it had already moved past.

        The waiver takes effect only when it still names the Pair these Fills
        have just counted, never a Pair that has since replaced it.

        Which Entries are open, rather than merely how many, because a Pair a
        sale broke up is read back off exactly that — a Cooldown lifted that
        way is not stored, it is restated by the Entries that are still here.

        Each open Entry brings the time its Fill happened, so a Cooldown that
        waits can be read back too. Nothing is stored for it: a Cooldown only
        stands while the Basket holds both Entries of the Pair, so the Fill
        that completed a standing Pair is always one of the Entries here.
        """
        lifecycle = cls(policy)
        filled_at_by_entry = dict(open_entries)
        lifecycle._entry_count = len(open_entries)
        lifecycle._filled_total = lifecycle._entry_count + settled_entry_count
        lifecycle._completed_pairs = lifecycle._filled_total // 2
        lifecycle._released_entries = (
            set(range(1, lifecycle._filled_total + 1)) - filled_at_by_entry.keys()
        )
        # None when the completing Fill has since been sold — and a Pair
        # missing an Entry is a Pair whose Cooldown has already ended, so no
        # Cooldown ever has to be answered for without a time to answer from.
        lifecycle._pair_completed_at = filled_at_by_entry.get(
            lifecycle._completed_pairs * 2
        )
        if waived_pair is not None:
            lifecycle._restore_waiver(waived_pair)
        return lifecycle

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def completed_pairs(self) -> int:
        return self._completed_pairs

    def entry_gate(self, as_of: datetime) -> EntryGate:
        """Whether the Basket may take another Entry as of a moment in time.

        A completed Pair closes the gate, and what opens it again is the
        Session's Cooldown Policy: never closed at all under `OFF`, the
        trader's say under `UNTIL_CANCELLED`, and either that or the days
        having passed under `DURATION`.

        The moment to answer as of is handed in rather than read off a clock,
        so this stays a function of the Basket and the market's own time — the
        same candles replayed give the same answer every time.
        """
        if self._entry_count >= self._policy.max_entries:
            return EntryGate(EntryGateState.MAX_ENTRIES_REACHED)
        if self._filled_total == 0 or self._filled_total % 2 == 1:
            return EntryGate(EntryGateState.OPEN)
        if self._policy.cooldown.mode is CooldownMode.OFF:
            return EntryGate(EntryGateState.OPEN)
        if self._waived_pair == self._completed_pairs:
            return EntryGate(EntryGateState.OPEN)
        if not self._holds_whole_pair(self._completed_pairs):
            return EntryGate(EntryGateState.OPEN)
        if self._has_lapsed(as_of):
            return EntryGate(EntryGateState.OPEN)
        return EntryGate(EntryGateState.COOLDOWN)

    def entry_gate_for(self, entry_number: int, as_of: datetime) -> EntryGate:
        """The gate as it stands for one Entry, not the Pair overall.

        A Cooldown blocks the next Entry, not the ones an earlier,
        already-resolved Pair left open — those Entries were never blocked by
        a Cooldown that only started after they filled.
        """
        gate = self.entry_gate(as_of)
        if gate.state is not EntryGateState.COOLDOWN:
            return gate
        if entry_pair_index(entry_number) < self._completed_pairs:
            return EntryGate(EntryGateState.OPEN)
        return gate

    def can_enter(self, as_of: datetime) -> bool:
        return self.entry_gate(as_of).is_open

    def cooldown_ends_at(self) -> datetime | None:
        """When the Cooldown standing over this Basket lifts on its own.

        None whenever nothing is waiting on the clock — no Pair has completed,
        the Policy is one that never lapses, or a sale has already broken up
        the Pair a Cooldown would have stood over.
        """
        if self._pair_completed_at is None:
            return None
        if not self._holds_whole_pair(self._completed_pairs):
            return None
        return self._policy.cooldown.ends_at(completed_at=self._pair_completed_at)

    def record_fill(self, filled_at: datetime) -> None:
        if not self.can_enter(filled_at):
            raise ValueError("entry is blocked by pair lifecycle")
        self._count_fill(filled_at)

    def _count_fill(self, filled_at: datetime) -> None:
        """Take a Fill into the count, whoever decided it should happen.

        Every second Fill completes a Pair, which is the rule the gate is read
        against. It is stated once here so the two ways in — a live Entry that
        passed the gate, and replay that never asks it — cannot drift apart.

        The Fill that completes a Pair is also when that Pair's Cooldown
        starts, so its market time is kept here for a Policy that waits.
        """
        self._entry_count += 1
        self._filled_total += 1
        if self._filled_total % 2 == 0:
            self._completed_pairs += 1
            self._pair_completed_at = filled_at

    def _has_lapsed(self, as_of: datetime) -> bool:
        if self._pair_completed_at is None:
            return False
        return self._policy.cooldown.has_lapsed(
            completed_at=self._pair_completed_at, as_of=as_of
        )

    def release_fill(self, entry_number: int) -> None:
        """Hand back the place an Entry held when that Entry is sold.

        Only the open count gives up its place; the Fill still counts toward
        whichever Pair it completed, so selling never unmakes or renames a Pair
        that has formed.

        What a sale does end is the Cooldown of the Pair it sold out of: that
        Cooldown says the Basket is holding both Entries of the Pair at once,
        which stops being true the moment either of them goes. Which is why the
        Entry says which Pair it came from — one sold out of an earlier,
        already-resolved Pair leaves a later Pair's Cooldown exactly where it
        was, because that later Pair is still whole.
        """
        if self._entry_count == 0:
            raise ValueError("no Entry is open to release")
        self._entry_count -= 1
        self._released_entries.add(entry_number)

    def waive_cooldown(self, as_of: datetime) -> None:
        """Let a trader open past the Cooldown blocking this Pair, on their say.

        It lifts exactly the Cooldown blocking the gate now, and the next Pair
        this Basket completes closes the gate again. Under a Policy that
        waits, this is the trader choosing not to wait out the rest of it;
        under one that never lapses, it is the only way the gate opens at all.
        """
        if self.entry_gate(as_of).state is not EntryGateState.COOLDOWN:
            raise ValueError("no Cooldown is blocking this Pair")
        self._waived_pair = self._completed_pairs

    def _holds_whole_pair(self, pair: int) -> bool:
        """Whether both Entries of a Pair are still open, which is what cools.

        Asked of the Entries a Pair is made of rather than of when a sale
        happened, so an Entry sold before its partner ever filled counts the
        same as one sold after: either way the Basket never came to hold both.
        """
        return not {pair * 2 - 1, pair * 2} & self._released_entries

    def _restore_waiver(self, completed_pairs: int) -> None:
        """Put back a waiver replay read from storage, exactly as it stood.

        Replay rebuilds a Pair from its Fills alone; a waiver is the one fact
        no Fill carries. It only takes effect when it still names the Pair
        replay has just counted, never a Pair that has since replaced it.
        """
        if self._completed_pairs == completed_pairs:
            self._waived_pair = completed_pairs

    def reset(self) -> None:
        self._entry_count = 0
        self._filled_total = 0
        self._completed_pairs = 0
        self._waived_pair = None
        self._released_entries = set()
        self._pair_completed_at = None
