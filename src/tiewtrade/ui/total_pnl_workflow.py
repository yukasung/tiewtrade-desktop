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

import logging
from collections.abc import Callable
from decimal import Decimal

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from tiewtrade.application.drawdown import current_drawdown
from tiewtrade.application.total_pnl import total_pnl
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.ui.background_task import BackgroundTask

_logger = logging.getLogger(__name__)

ReadTotalRealizedPnl = Callable[[], Decimal]
ReadHighWaterMark = Callable[[], Decimal]
RecordHighWaterMark = Callable[[Decimal], Decimal]


class TotalPnlWorkflow(QObject):
    """Holds both halves of the total and says what they come to."""

    #: The total as it now reads, or None while there is no answer to give.
    total_changed = Signal(object)

    #: The distance back to the highest Total P/L this installation has ever
    #: reached, or no answer while either side of that is unknown.
    drawdown_changed = Signal(object)

    def __init__(
        self,
        *,
        read_total_realized_pnl: ReadTotalRealizedPnl | None = None,
        read_high_water_mark: ReadHighWaterMark | None = None,
        record_high_water_mark: RecordHighWaterMark | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._read_total_realized_pnl = read_total_realized_pnl
        self._read_high_water_mark = read_high_water_mark
        self._record_high_water_mark = record_high_water_mark
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._realized: Decimal | None = None
        # The peak read at startup, then kept up to date locally from every
        # total that beats it — read again only once, because after that a
        # record's own return is the freshest answer there is.
        self._high_water_mark: Decimal | None = None
        self._high_water_mark_requested = False
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
        self._drawdown_published = False
        self._last_drawdown_published: Decimal | None = None

    @Slot()
    def start(self) -> None:
        self._request_high_water_mark()
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

    def _request_high_water_mark(self) -> None:
        read = self._read_high_water_mark
        if read is None or self._high_water_mark_requested:
            return
        self._high_water_mark_requested = True
        task = BackgroundTask(read)
        task.signals.succeeded.connect(self._high_water_mark_landed)
        self._thread_pool.start(task)

    @Slot(object)
    def _high_water_mark_landed(self, value: object) -> None:
        if self._closed or not isinstance(value, Decimal):
            return
        self._high_water_mark = value
        self._publish()

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
        # A peak that has just landed changes what drawdown reads without
        # changing the total itself, so the two are deduplicated apart: a
        # total unchanged since the last publish would otherwise swallow the
        # one moment the peak had anything new to say.
        if not self._published or value != self._last_published:
            self._published = True
            self._last_published = value
            self.total_changed.emit(value)
        self._publish_drawdown(value)

    def _publish_drawdown(self, value: Decimal | None) -> None:
        peak = self._high_water_mark
        if (
            value is not None
            and peak is not None
            and value > peak
            and self._record_high_water_mark is not None
        ):
            # Shown at once against the total that just beat the peak, rather
            # than waiting on a write nobody watching this figure should have
            # to wait on: the total earned this peak whether or not the file
            # ever confirms it.
            self._high_water_mark = value
            peak = value
            self._record_new_high_water_mark(value)
        drawdown = current_drawdown(high_water_mark=peak, current_total=value)
        if self._drawdown_published and drawdown == self._last_drawdown_published:
            return
        self._drawdown_published = True
        self._last_drawdown_published = drawdown
        self.drawdown_changed.emit(drawdown)

    def _record_new_high_water_mark(self, value: Decimal) -> None:
        record = self._record_high_water_mark
        assert record is not None
        task = BackgroundTask(lambda: record(value))
        task.signals.failed.connect(self._high_water_mark_record_failed)
        self._thread_pool.start(task)

    @Slot(object)
    def _high_water_mark_record_failed(self, _error: object) -> None:
        # The screen already shows the peak this total earned; only the copy
        # on disk did not confirm it. Worth knowing, not worth acting on.
        _logger.warning("equity_high_water_mark.record_failed")

    def _price_for(self, basket: BasketSnapshot | None) -> Decimal | None:
        """The live price, but only where it is a price for this Basket."""
        if basket is None or self._price_symbol != basket.symbol:
            return None
        return self._price
