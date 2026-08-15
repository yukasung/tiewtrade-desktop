from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from tiewtrade.trading.entry_policy import EntryPolicy


def month_index(at: datetime) -> int:
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return (at.year * 12) + at.month


def month_start(index: int) -> date:
    """Turn a month index back into the first day of that month.

    The index is arithmetic the lifecycle does on itself; a reader outside it
    needs a month it can name.
    """
    year, month = divmod(index - 1, 12)
    return date(year, month + 1, 1)


class EntryGateState(StrEnum):
    OPEN = "open"
    COOLDOWN = "cooldown"
    MAX_ENTRIES_REACHED = "max_entries_reached"


@dataclass(frozen=True, slots=True)
class EntryGate:
    """Whether an Entry may open now, and when a closed gate reopens.

    A blocked gate carries which of the two rules blocked it, because they
    mean opposite things to a reader: a Cooldown Month passes on its own,
    while a Session out of Entries never opens again.
    """

    state: EntryGateState
    reopens_at: date | None = None

    def __post_init__(self) -> None:
        if (self.state is EntryGateState.COOLDOWN) != (self.reopens_at is not None):
            raise ValueError("reopens_at belongs to a Cooldown Month and only to one")

    @property
    def is_open(self) -> bool:
        return self.state is EntryGateState.OPEN


class EntryPairLifecycle:
    def __init__(self, policy: EntryPolicy) -> None:
        self._policy = policy
        self._entry_count = 0
        self._completed_pair_month: int | None = None

    @property
    def entry_count(self) -> int:
        return self._entry_count

    def entry_gate(self, at: datetime) -> EntryGate:
        current_month = month_index(at)
        if self._entry_count >= self._policy.max_entries:
            return EntryGate(EntryGateState.MAX_ENTRIES_REACHED)
        if self._entry_count == 0 or self._entry_count % 2 == 1:
            return EntryGate(EntryGateState.OPEN)

        if self._completed_pair_month is None:
            raise RuntimeError("completed Entry Pair is missing its completion month")
        next_pair_month = self._completed_pair_month + 2
        if current_month >= next_pair_month:
            return EntryGate(EntryGateState.OPEN)
        return EntryGate(EntryGateState.COOLDOWN, month_start(next_pair_month))

    def can_enter(self, at: datetime) -> bool:
        return self.entry_gate(at).is_open

    def record_fill(self, at: datetime) -> None:
        if not self.can_enter(at):
            raise ValueError("entry is blocked by pair lifecycle")

        self._entry_count += 1
        if self._entry_count % 2 == 0:
            self._completed_pair_month = month_index(at)

    def release_fill(self) -> None:
        """Hand back the place an Entry held when that Entry is sold.

        The count is what a quota and an Entry Pair are read against, so an
        Entry that leaves the Basket leaves the count too — which is what lets
        a Pair reopen the moment one of its Entries takes profit.
        """
        if self._entry_count == 0:
            raise ValueError("no Entry is open to release")
        self._entry_count -= 1
        if self._entry_count % 2 == 1:
            self._completed_pair_month = None

    def reset(self) -> None:
        self._entry_count = 0
        self._completed_pair_month = None
