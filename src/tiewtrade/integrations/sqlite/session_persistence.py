from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tiewtrade.application.session_persistence import (
    PersistenceState,
    PersistentSessionSnapshot,
    SessionPersistenceBlockedError,
)
from tiewtrade.market_data.candle import Candle


class _CandleProcessor[ProcessorSnapshotT](Protocol):
    def process_completed_candle(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> ProcessorSnapshotT:
        pass

    def process_in_progress_candle(
        self,
        candle: Candle,
        *,
        observed_at: datetime,
    ) -> ProcessorSnapshotT:
        pass

    def waive_cooldown(self, observed_at: datetime) -> ProcessorSnapshotT:
        pass


class _EntryClosingProcessor[ProcessorSnapshotT](
    _CandleProcessor[ProcessorSnapshotT], Protocol
):
    def close_entry(
        self,
        entry_number: int,
        *,
        price: Decimal,
        observed_at: datetime,
    ) -> ProcessorSnapshotT:
        pass


class SQLiteSessionPersistenceCoordinator[CoordinatorSnapshotT]:
    def __init__(
        self,
        session: _CandleProcessor[CoordinatorSnapshotT],
        record_snapshot: Callable[[CoordinatorSnapshotT], None],
    ) -> None:
        self._session = session
        self._record_snapshot = record_snapshot
        self._state = PersistenceState.READY

    def process_completed_candle(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> PersistentSessionSnapshot[CoordinatorSnapshotT]:
        return self._recorded(
            lambda: self._session.process_completed_candle(
                candle,
                received_at=received_at,
            )
        )

    def process_in_progress_candle(
        self,
        candle: Candle,
        *,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[CoordinatorSnapshotT]:
        return self._recorded(
            lambda: self._session.process_in_progress_candle(
                candle,
                observed_at=observed_at,
            )
        )

    def waive_cooldown(
        self, observed_at: datetime
    ) -> PersistentSessionSnapshot[CoordinatorSnapshotT]:
        return self._recorded(lambda: self._session.waive_cooldown(observed_at))

    def _recorded(
        self,
        process: Callable[[], CoordinatorSnapshotT],
    ) -> PersistentSessionSnapshot[CoordinatorSnapshotT]:
        """An exit taken between candles is durable on the same terms as one
        taken at a close: recorded before it is published, or the Session is
        blocked."""
        if self._state is PersistenceState.BLOCKED:
            raise SessionPersistenceBlockedError(
                "Session is blocked because Trade History persistence failed"
            )

        snapshot = process()
        try:
            self._record_snapshot(snapshot)
        except Exception:
            self._state = PersistenceState.BLOCKED
            raise

        return PersistentSessionSnapshot(
            session=snapshot,
            persistence_state=self._state,
        )


class SQLiteEntryClosingCoordinator[CoordinatorSnapshotT](
    SQLiteSessionPersistenceCoordinator[CoordinatorSnapshotT]
):
    """The same terms for a sale nobody's candle asked for.

    An Entry sold on demand is durable the way every other exit here is:
    recorded before it is published, or the Session is blocked.
    """

    def __init__(
        self,
        session: _EntryClosingProcessor[CoordinatorSnapshotT],
        record_snapshot: Callable[[CoordinatorSnapshotT], None],
    ) -> None:
        super().__init__(session, record_snapshot)
        self._entry_closer = session

    def close_entry(
        self,
        entry_number: int,
        *,
        price: Decimal,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[CoordinatorSnapshotT]:
        return self._recorded(
            lambda: self._entry_closer.close_entry(
                entry_number,
                price=price,
                observed_at=observed_at,
            )
        )
