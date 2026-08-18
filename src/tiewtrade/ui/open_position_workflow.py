"""The Position a Session holds while no Bot is running, and what it reads as.

A Basket outlives the Bot that opened it: it is in the database before anything
starts, it is still there after a stop, and it goes on being worth something as
the price moves. Nothing publishes it in that state, so it is read here and
marked on the newest price the chart has.

The Position tab has two writers — the Runtime's snapshot while it runs, and
this one the rest of the time — and only one of them can be right at a time.
Keeping this side in one object is what makes the rule statable: a Basket that
is open is never reported as no Position, whatever else has just painted the
tab. Ordering inside a window cannot carry a rule like that.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from tiewtrade.application.chart_data import ChartCandle
from tiewtrade.application.open_basket import ReopenedBasket
from tiewtrade.application.paper_basket_projection import reopened_basket_snapshot
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trading_workspace import (
    POSITION_BASKET_UNREADABLE_MESSAGE,
    empty_position_basket_tab,
    failed_position_basket_tab,
    loading_position_basket_tab,
    ready_position_basket_tab,
)
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.take_profit_confirmation import (
    leaves_entries_holding,
    position_at_target_message,
    position_held_message,
    sells_on_start_message,
)

ReadOpenBasket = Callable[[ConfiguredPaperSession], ReopenedBasket | None]


class OpenPositionWorkflow(QObject):
    """Reads the Basket a Session is holding and says how it reads now."""

    snapshot_changed = Signal(object)

    def __init__(
        self,
        *,
        read_open_basket: ReadOpenBasket | None = None,
        thread_pool: QThreadPool | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._read_open_basket = read_open_basket
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._clock = clock
        self._basket: ReopenedBasket | None = None
        self._task: BackgroundTask | None = None
        self._reread: ConfiguredPaperSession | None = None
        self._candle: ChartCandle | None = None
        # Nothing has been asked for yet, so nothing is held and nothing is
        # unknown. Only a read that is out or that failed makes it unknown.
        self._holding_is_unknown = False

    @property
    def basket(self) -> ReopenedBasket | None:
        """The Basket held right now, or none while the Runtime holds it."""
        return self._basket

    def read_for(self, session: ConfiguredPaperSession) -> None:
        """Ask the database what this Session is holding.

        Reading touches the database, which is a worker's job rather than the
        Qt thread's, so the answer arrives later and the Basket is dropped in
        the meantime: what was held may be what has just been sold.
        """
        self._basket = None
        read_open_basket = self._read_open_basket
        if read_open_basket is None:
            return
        # Between here and the answer, what is held is not known — which is
        # not the same as knowing nothing is held.
        self._holding_is_unknown = True
        if self._task is not None:
            # A read is already on its way, and the Basket it saw may be older
            # than the one being asked about now. It is asked again when that
            # one lands, rather than dropped and the tab left saying nothing.
            self._reread = session
            return
        self._reread = None
        task = BackgroundTask(lambda: read_open_basket(session))
        task.signals.succeeded.connect(self._read_landed)
        task.signals.failed.connect(self._read_failed)
        task.signals.finished.connect(self._read_finished)
        self._task = task
        self._thread_pool.start(task)

    def mark_price(self, candle: ChartCandle) -> None:
        """Take the newest price a resting Basket is marked on."""
        self._candle = candle
        self.reassert()

    def held_by_runtime(self) -> None:
        """The Runtime owns the Basket now and publishes it every candle."""
        self._basket = None
        self._holding_is_unknown = False

    def forget(self) -> None:
        """Drop a Basket that has just been closed.

        Reading a new one takes a trip to the database, and until it lands the
        copy kept here describes something that is no longer there.
        """
        self._basket = None
        self._holding_is_unknown = False

    def sells_on_start_message(self) -> str | None:
        """What starting a Bot would sell at once, or nothing to say about it.

        No Basket, or no price to judge one by, both answer the same way:
        there is nothing a question could truthfully be about.
        """
        basket = self._basket
        candle = self._candle
        if basket is None or candle is None:
            return None
        return sells_on_start_message(basket, candle.close)

    def position_at_target_message(self) -> str | None:
        """What the held Position has reached, or nothing to say about it.

        Answers as one with sells_on_start_message: the same Basket and the
        same price decide both, so a question is never put about a Position
        the other reads as holding.
        """
        basket = self._basket
        candle = self._candle
        if basket is None or candle is None:
            return None
        return position_at_target_message(basket, candle.close)

    def position_held_message(self) -> str | None:
        """How much is open, or nothing to say about it.

        A count needs no price, so this one answers from the Basket alone. The
        two beside it still need one: what a Start would sell is a question
        about where the price stands, not about what is held.
        """
        basket = self._basket
        if basket is None:
            return None
        return position_held_message(basket)

    def leaves_entries_holding(self) -> bool:
        """Whether a Start would leave part of the Position unsold.

        Nothing to read it from answers the same way as a Basket a Start would
        take whole: the question that follows is then the one that offers no
        Start at all, which is the answer to be wrong towards.
        """
        basket = self._basket
        candle = self._candle
        if basket is None or candle is None:
            return False
        return leaves_entries_holding(basket, candle.close)

    def what_a_start_sells_is_unknown(self) -> bool:
        """Whether a Start could sell at a target without this saying so.

        A Basket read that has not landed, one that failed, and a Basket with
        no price to judge it by all answer the same way: nothing here can rule
        the sale out. Reading that silence as "nothing to ask about" is what
        let a Position go at its target with no question put.
        """
        if self._holding_is_unknown:
            return True
        return self._basket is not None and self._candle is None

    def reassert(self) -> None:
        """Say again what is held, over whatever has just painted the tab.

        A stopped Runtime holds no Basket, so its snapshot reads as an empty
        tab. Anything held is put back straight after, because "No open
        Position" over an open one is the single answer that is never true.
        """
        basket = self._basket
        if basket is None:
            return
        candle = self._candle
        if candle is None:
            # Held, but with no price to mark it on yet. Waiting is the true
            # answer; an empty tab would be the untrue one.
            self.snapshot_changed.emit(
                loading_position_basket_tab(empty_position_basket_tab())
            )
            return
        observed_at = self._clock()
        self.snapshot_changed.emit(
            ready_position_basket_tab(
                reopened_basket_snapshot(
                    basket,
                    symbol=candle.symbol,
                    current_price=candle.close,
                    observed_at_utc=observed_at,
                ),
                observed_at_utc=observed_at,
            )
        )

    @Slot(object)
    def _read_landed(self, result: object) -> None:
        if result is not None and not isinstance(result, ReopenedBasket):
            return
        self._basket = result
        self._holding_is_unknown = False
        self.reassert()

    @Slot(object)
    def _read_failed(self, _error: object) -> None:
        """Say that a Position is there but out of reach, rather than nothing.

        An empty tab would read as "no Position", which is the one thing that
        is not true: it is open, and only closing it clears the way.
        """
        self._basket = None
        self._holding_is_unknown = True
        self.snapshot_changed.emit(
            failed_position_basket_tab(
                empty_position_basket_tab(),
                POSITION_BASKET_UNREADABLE_MESSAGE,
            )
        )

    @Slot()
    def _read_finished(self) -> None:
        self._task = None
        session = self._reread
        if session is not None:
            self.read_for(session)
