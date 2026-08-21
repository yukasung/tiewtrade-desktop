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


class EntryPairLifecycle:
    def __init__(self, policy: EntryPolicy) -> None:
        self._policy = policy
        self._entry_count = 0
        # Pairs this Basket has completed, counted forward and never back. An
        # Entry that sells reopens the gate without unmaking the Pair it was
        # part of, which is what lets a waiver name one Cooldown and not the
        # Cooldown that replaces it.
        self._completed_pairs = 0
        self._waived_pair: int | None = None

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
        if self._entry_count == 0 or self._entry_count % 2 == 1:
            return EntryGate(EntryGateState.OPEN)
        if self._waived_pair == self._completed_pairs:
            return EntryGate(EntryGateState.OPEN)
        return EntryGate(EntryGateState.COOLDOWN)

    def can_enter(self) -> bool:
        return self.entry_gate().is_open

    def record_fill(self) -> None:
        if not self.can_enter():
            raise ValueError("entry is blocked by pair lifecycle")

        self._entry_count += 1
        if self._entry_count % 2 == 0:
            self._completed_pairs += 1

    def release_fill(self) -> None:
        """Hand back the place an Entry held when that Entry is sold.

        The count is what a quota and an Entry Pair are read against, so an
        Entry that leaves the Basket leaves the count too — which is what lets
        a Pair reopen the moment one of its Entries takes profit.
        """
        if self._entry_count == 0:
            raise ValueError("no Entry is open to release")
        self._entry_count -= 1

    def waive_cooldown(self) -> None:
        """Let a trader open past the Cooldown blocking this Pair, on their say.

        A Cooldown stands until it is waived, so this is the only way one is
        lifted — and it lifts exactly the one blocking the gate now. The next
        Pair this Basket completes closes the gate again.
        """
        if self.entry_gate().state is not EntryGateState.COOLDOWN:
            raise ValueError("no Cooldown is blocking this Pair")
        self._waived_pair = self._completed_pairs

    def restore_waiver(self, completed_pairs: int) -> None:
        """Put back a waiver replay read from storage, exactly as it stood.

        Replay rebuilds a Pair from its Fills alone; a waiver is the one fact
        no Fill carries. It only takes effect when it still names the Pair
        replay has just counted, never a Pair that has since replaced it.
        """
        if self._completed_pairs == completed_pairs:
            self._waived_pair = completed_pairs

    def reset(self) -> None:
        self._entry_count = 0
        self._completed_pairs = 0
        self._waived_pair = None
