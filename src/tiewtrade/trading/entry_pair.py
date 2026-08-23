from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

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

    @classmethod
    def replayed(
        cls,
        policy: EntryPolicy,
        *,
        open_entry_numbers: Sequence[int],
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
        """
        lifecycle = cls(policy)
        lifecycle._entry_count = len(open_entry_numbers)
        lifecycle._filled_total = lifecycle._entry_count + settled_entry_count
        lifecycle._completed_pairs = lifecycle._filled_total // 2
        lifecycle._released_entries = set(range(1, lifecycle._filled_total + 1)) - set(
            open_entry_numbers
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

    def entry_gate(self) -> EntryGate:
        """Whether the Basket may take another Entry as it stands.

        A completed Pair closes the gate and nothing but the trader opens it:
        a Cooldown does not lapse on its own, so the answer here turns on what
        the Basket holds and what the trader has said, never on the clock.
        """
        if self._entry_count >= self._policy.max_entries:
            return EntryGate(EntryGateState.MAX_ENTRIES_REACHED)
        if self._filled_total == 0 or self._filled_total % 2 == 1:
            return EntryGate(EntryGateState.OPEN)
        if self._waived_pair == self._completed_pairs:
            return EntryGate(EntryGateState.OPEN)
        if not self._holds_whole_pair(self._completed_pairs):
            return EntryGate(EntryGateState.OPEN)
        return EntryGate(EntryGateState.COOLDOWN)

    def entry_gate_for(self, entry_number: int) -> EntryGate:
        """The gate as it stands for one Entry, not the Pair overall.

        A Cooldown blocks the next Entry, not the ones an earlier,
        already-resolved Pair left open — those Entries were never blocked by
        a Cooldown that only started after they filled.
        """
        gate = self.entry_gate()
        if gate.state is not EntryGateState.COOLDOWN:
            return gate
        if entry_pair_index(entry_number) < self._completed_pairs:
            return EntryGate(EntryGateState.OPEN)
        return gate

    def can_enter(self) -> bool:
        return self.entry_gate().is_open

    def record_fill(self) -> None:
        if not self.can_enter():
            raise ValueError("entry is blocked by pair lifecycle")
        self._count_fill()

    def _count_fill(self) -> None:
        """Take a Fill into the count, whoever decided it should happen.

        Every second Fill completes a Pair, which is the rule the gate is read
        against. It is stated once here so the two ways in — a live Entry that
        passed the gate, and replay that never asks it — cannot drift apart.
        """
        self._entry_count += 1
        self._filled_total += 1
        if self._filled_total % 2 == 0:
            self._completed_pairs += 1

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

    def waive_cooldown(self) -> None:
        """Let a trader open past the Cooldown blocking this Pair, on their say.

        A Cooldown stands until it is waived, so this is the only way one is
        lifted — and it lifts exactly the one blocking the gate now. The next
        Pair this Basket completes closes the gate again.
        """
        if self.entry_gate().state is not EntryGateState.COOLDOWN:
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
