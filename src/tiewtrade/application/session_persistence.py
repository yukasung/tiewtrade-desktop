from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from tiewtrade.market_data.candle import Candle


class PersistenceState(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class SessionPersistenceBlockedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PersistentSessionSnapshot[SessionSnapshotT]:
    session: SessionSnapshotT
    persistence_state: PersistenceState


class SessionPersistenceCoordinator[SessionSnapshotT](Protocol):
    def process_completed_candle(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool,
    ) -> PersistentSessionSnapshot[SessionSnapshotT]:
        pass

    def process_in_progress_candle(
        self,
        candle: Candle,
        *,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[SessionSnapshotT]:
        pass

    def waive_cooldown(
        self,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[SessionSnapshotT]:
        """Lift the Cooldown holding this Session's Basket, on the trader's say.

        Every market can be asked: a Cooldown is the Entry Pair rule, which
        Spot and Futures share, and it is lifted the same way in both.
        """


class EntryClosingPersistence[SessionSnapshotT](
    SessionPersistenceCoordinator[SessionSnapshotT], Protocol
):
    """A Session that also sells an Entry because the trader asked it to.

    Kept apart from the candle-driven pair because not every market can do
    it: Paper Futures has no way to take one Entry out of a position, so it
    has no way to be asked.
    """

    def close_entry(
        self,
        entry_number: int,
        *,
        price: Decimal,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[SessionSnapshotT]:
        pass
