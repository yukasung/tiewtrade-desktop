from tiewtrade.application.paper_futures_session import (
    PaperFuturesSession,
    PaperFuturesSessionSnapshot,
)
from tiewtrade.application.session_persistence import SessionPersistenceCoordinator
from tiewtrade.integrations.sqlite.paper_entry_exit import (
    entry_exits_with_closing_basket,
)
from tiewtrade.integrations.sqlite.paper_futures_history import (
    PaperFuturesSQLiteHistory,
)
from tiewtrade.integrations.sqlite.session_persistence import (
    SQLiteSessionPersistenceCoordinator,
)


class _PaperFuturesSnapshotRecorder:
    def __init__(self, history: PaperFuturesSQLiteHistory) -> None:
        self._history = history

    def record(self, snapshot: PaperFuturesSessionSnapshot) -> None:
        if snapshot.entry_fill is not None:
            if snapshot.basket_id is None:
                raise ValueError("entry Fill requires a Basket ID")
            entry_number = snapshot.basket_entry_count
            if snapshot.closed_basket is not None:
                entry_number = snapshot.closed_basket.entry_count
            self._history.record_entry(
                basket_id=snapshot.basket_id,
                entry_number=entry_number,
                fill=snapshot.entry_fill,
            )

        if not snapshot.entry_exits:
            if snapshot.closed_basket is not None:
                raise ValueError("a closed Basket requires the Exits that emptied it")
            return
        self._record_entry_exits(snapshot)

    def _record_entry_exits(self, snapshot: PaperFuturesSessionSnapshot) -> None:
        """Write each Entry's sale in the order the Basket made them."""
        for entry_exit, basket_id, closed_basket in entry_exits_with_closing_basket(
            snapshot.entry_exits,
            basket_id=snapshot.basket_id,
            closed_basket=snapshot.closed_basket,
        ):
            self._history.record_entry_exit(
                basket_id=basket_id,
                fill=entry_exit.fill,
                closed=entry_exit.closed,
                closed_basket=closed_basket,
            )


def create_persistent_paper_futures_session(
    session: PaperFuturesSession,
    history: PaperFuturesSQLiteHistory,
) -> SessionPersistenceCoordinator[PaperFuturesSessionSnapshot]:
    if session.identity != history.session_identity:
        raise ValueError("Paper Futures Session and Trade History identity differ")
    recorder = _PaperFuturesSnapshotRecorder(history)
    return SQLiteSessionPersistenceCoordinator(session, recorder.record)
