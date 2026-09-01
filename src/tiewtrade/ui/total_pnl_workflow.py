"""The one figure that answers "how is this doing", and what keeps it current.

Half of it is durable and half of it is not. The realised half is a read of
the database, so it goes to a worker and comes back later; the unrealised
half arrives with every candle already computed, on the Qt thread, for free.
Keeping them apart is what lets the price move every few seconds without the
database being asked anything.

The unrealised half is marked again on every live price, which is what keeps
the figure moving between candles. Nothing else re-marks a Basket a running
Runtime owns, so without it the total sat still for a whole timeframe while
the price beside it moved.

The realised half only changes when an Entry is sold, and every sale changes
which Entries the open Basket is holding. So the window watches that
composition and asks again when it moves — there is nothing to poll for.
"""

from collections.abc import Callable
from decimal import Decimal

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from tiewtrade.application.total_pnl import total_pnl
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.ui.background_task import BackgroundTask

ReadTotalRealizedPnl = Callable[[], Decimal]


class TotalPnlWorkflow(QObject):
    """Holds both halves of the total and says what they come to."""

    #: The total as it now reads, or None while there is no answer to give.
    total_changed = Signal(object)

    def __init__(
        self,
        *,
        read_total_realized_pnl: ReadTotalRealizedPnl | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._read_total_realized_pnl = read_total_realized_pnl
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._realized: Decimal | None = None
        self._basket: BasketSnapshot | None = None
        # The newest price the chart has, kept beside the Basket rather than
        # folded into it: it belongs to whatever Pair the chart is showing,
        # which is not always the Pair the Basket was taken in.
        self._price_symbol: str | None = None
        self._price: Decimal | None = None
        self._task: BackgroundTask | None = None
        # Asked again while a read was out: the answer in flight saw the
        # database before whatever prompted the second ask, so one more read
        # follows it — one, however many times it was asked.
        self._pending = False
        self._closed = False
        # Nothing has been published yet, and None is a value this publishes,
        # so "not yet" cannot be spelled with it.
        self._published = False
        self._last_published: Decimal | None = None

    @Slot()
    def start(self) -> None:
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Ask the database what has been realised, if it is not already out."""
        read = self._read_total_realized_pnl
        if read is None or self._closed:
            return
        if self._task is not None:
            self._pending = True
            return
        self._pending = False
        task = BackgroundTask(read)
        task.signals.succeeded.connect(self._read_landed)
        task.signals.failed.connect(self._read_failed)
        task.signals.finished.connect(self._read_finished)
        self._task = task
        self._thread_pool.start(task)

    def set_open_basket(self, basket: BasketSnapshot | None) -> None:
        """Take the Basket the Position tab is showing, marked as it stands."""
        if self._closed:
            return
        self._basket = basket
        self._publish()

    def set_current_price(self, symbol: str, price: Decimal) -> None:
        """Take the newest price, which re-marks the Basket without a read.

        A price that is not a price at all — zero, negative, not finite — is
        dropped rather than marked on: the mark would raise, on the Qt thread,
        for a figure that is only being displayed.
        """
        if self._closed:
            return
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            return
        if symbol == self._price_symbol and price == self._price:
            return
        self._price_symbol = symbol
        self._price = price
        self._publish()

    def clear_price(self) -> None:
        """Drop the live price, leaving the Basket as it was last marked."""
        if self._price_symbol is None and self._price is None:
            return
        self._price_symbol = None
        self._price = None
        self._publish()

    @Slot()
    def close(self) -> None:
        """Stop, and drop whatever is still on its way back."""
        self._closed = True
        self._pending = False

    @Slot(object)
    def _read_landed(self, value: object) -> None:
        if self._closed or not isinstance(value, Decimal):
            return
        self._realized = value
        self._publish()

    @Slot(object)
    def _read_failed(self, _error: object) -> None:
        if self._closed:
            return
        # A total that outlives the read behind it is a number with nothing
        # holding it up, and it looks exactly like one that is still true.
        self._realized = None
        self._publish()

    @Slot()
    def _read_finished(self) -> None:
        self._task = None
        if self._pending and not self._closed:
            self.refresh()

    def _publish(self) -> None:
        if self._read_total_realized_pnl is None:
            # Nothing can ever be read, so there is no total to be had and
            # none to report on: the card keeps its blank.
            return
        value = total_pnl(
            realized=self._realized,
            open_basket=self._basket,
            current_price=self._price_for(self._basket),
        )
        if self._published and value == self._last_published:
            return
        self._published = True
        self._last_published = value
        self.total_changed.emit(value)

    def _price_for(self, basket: BasketSnapshot | None) -> Decimal | None:
        """The live price, but only where it is a price for this Basket."""
        if basket is None or self._price_symbol != basket.symbol:
            return None
        return self._price
