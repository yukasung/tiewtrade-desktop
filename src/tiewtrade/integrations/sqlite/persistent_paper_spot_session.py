from tiewtrade.application.paper_spot_session import (
    PaperSpotSession,
    PaperSpotSessionSnapshot,
)
from tiewtrade.application.session_persistence import EntryClosingPersistence
from tiewtrade.integrations.sqlite.paper_entry_exit import record_paper_entry_exits
from tiewtrade.integrations.sqlite.paper_spot_history import PaperSpotSQLiteHistory
from tiewtrade.integrations.sqlite.session_persistence import (
    SQLiteEntryClosingCoordinator,
)


class _PaperSpotSnapshotRecorder:
    def __init__(self, history: PaperSpotSQLiteHistory) -> None:
        self._history = history

    def record(self, snapshot: PaperSpotSessionSnapshot) -> None:
        if snapshot.waived_pair is not None:
            if snapshot.basket_id is None:
                raise ValueError("cooldown waiver requires a Basket ID")
            if snapshot.waived_at is None:
                raise ValueError("cooldown waiver requires the moment it was granted")
            self._history.record_cooldown_waiver(
                basket_id=snapshot.basket_id,
                waived_pair=snapshot.waived_pair,
                waived_at=snapshot.waived_at,
            )
            return
        if snapshot.entry_fill is not None:
            if snapshot.basket_id is None:
                raise ValueError("entry Fill requires a Basket ID")
            if snapshot.entry_atr is None:
                raise ValueError("entry Fill requires the ATR it was read against")
            if snapshot.entry_number is None:
                raise ValueError("entry Fill requires its Entry number")
            if (
                snapshot.entry_group_id is None
                or snapshot.entry_group_take_profit_price is None
            ):
                raise ValueError("entry Fill requires the Group it bought into")
            self._history.record_entry(
                basket_id=snapshot.basket_id,
                entry_number=snapshot.entry_number,
                fill=snapshot.entry_fill,
                entry_atr=snapshot.entry_atr,
                entry_group_id=snapshot.entry_group_id,
                group_take_profit_price=snapshot.entry_group_take_profit_price,
            )
        if snapshot.entry_exits:
            if snapshot.exit_fill is not None:
                raise ValueError("an Entry Exit cannot accompany a whole-Basket Exit")
            record_paper_entry_exits(
                self._history,
                snapshot.entry_exits,
                basket_id=snapshot.basket_id,
                closed_basket=snapshot.closed_basket,
            )
            return
        if snapshot.exit_fill is None and snapshot.closed_basket is None:
            return
        if snapshot.exit_fill is None or snapshot.closed_basket is None:
            raise ValueError("Exit Fill and closed Basket must be present together")
        if snapshot.basket_id != snapshot.closed_basket.basket_id:
            raise ValueError("closed Basket requires a matching Basket ID")
        self._history.record_close(
            basket_id=snapshot.closed_basket.basket_id,
            fill=snapshot.exit_fill,
            closed=snapshot.closed_basket,
        )


def create_persistent_paper_spot_session(
    session: PaperSpotSession,
    history: PaperSpotSQLiteHistory,
) -> EntryClosingPersistence[PaperSpotSessionSnapshot]:
    if session.identity != history.session_identity:
        raise ValueError("Paper Spot Session and Trade History identity differ")
    recorder = _PaperSpotSnapshotRecorder(history)
    return SQLiteEntryClosingCoordinator(session, recorder.record)
