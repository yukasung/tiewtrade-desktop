from collections.abc import Callable
from decimal import Decimal
from enum import Enum, auto
from typing import TypeGuard

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.trading.trade_history import BasketResult
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.trade_history_presenter import (
    TradeHistoryFilterValues,
    trade_history_filter,
)

ListBasketHistory = Callable[[TradeHistoryFilter], BasketHistoryResult]
ListTradeFills = Callable[[TradeHistoryFilter], tuple[TradeFillHistoryItem, ...]]


class HistoryTab(Enum):
    BASKETS = auto()
    TRADES = auto()


class TradeHistoryWorkflow(QObject):
    """Reads one tab of durable history at a time, off the Qt thread.

    Both tabs answer the same filters and follow the same rule: a reply is
    delivered only while it is still the reply being waited for. A filter
    changed mid-flight bumps the generation, so the reply in transit is
    dropped rather than painted over the newer request.
    """

    baskets_loading = Signal(bool)
    baskets_ready = Signal(object)
    baskets_empty = Signal(object)
    baskets_unavailable = Signal(str)
    filter_invalid = Signal(str)
    trades_ready = Signal(object)
    trades_empty = Signal()
    trades_unavailable = Signal(str)
    trades_loading = Signal(bool)

    def __init__(
        self,
        *,
        list_basket_history: ListBasketHistory | None = None,
        list_trade_fills: ListTradeFills | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._list_basket_history = list_basket_history
        self._list_trade_fills = list_trade_fills
        self._active_tab = HistoryTab.BASKETS
        self._thread_pool = (
            thread_pool if thread_pool is not None else QThreadPool.globalInstance()
        )

        self._filters = TradeHistoryFilter()
        self._closed = False
        self._baskets_are_loading = False

        self._basket_generation = 0
        self._basket_tasks: dict[int, BackgroundTask] = {}
        self._latest_basket_filters: TradeHistoryFilter | None = None
        self._pending_basket_filters: TradeHistoryFilter | None = None
        self._failed_basket_filters: TradeHistoryFilter | None = None
        self._trade_generation = 0
        self._trade_tasks: dict[int, BackgroundTask] = {}
        self._latest_trade_filters: TradeHistoryFilter | None = None
        self._pending_trade_filters: TradeHistoryFilter | None = None
        self._failed_trade_filters: TradeHistoryFilter | None = None

    @Slot()
    def start(self) -> None:
        """Read the half that was opened, which is not always the first one."""
        self._request_active_tab()

    def apply_filters(self, values: TradeHistoryFilterValues) -> None:
        if self._closed:
            return
        try:
            filters = trade_history_filter(values)
        except OverflowError:
            self.filter_invalid.emit("To Date is out of range")
            return
        except ValueError as error:
            self.filter_invalid.emit(str(error))
            return

        self._filters = filters
        self._request_active_tab()

    @Slot()
    def reset_filters(self) -> None:
        if self._closed:
            return
        self._filters = TradeHistoryFilter()
        self._request_active_tab()

    def select_tab(self, tab: HistoryTab) -> None:
        if self._closed or tab is self._active_tab:
            return
        self._active_tab = tab
        self._request_active_tab()

    def _request_active_tab(self) -> None:
        if self._active_tab is HistoryTab.TRADES:
            self._request_trades()
            return
        self._request_basket_history()

    def _request_trades(self) -> None:
        list_trade_fills = self._list_trade_fills
        if list_trade_fills is None:
            return
        filters = self._filters
        if self._trade_tasks:
            if filters != self._latest_trade_filters:
                self._trade_generation += 1
                self._latest_trade_filters = filters
                self._pending_trade_filters = filters
            return
        self._start_trade_task(filters)

    def _start_trade_task(self, filters: TradeHistoryFilter) -> None:
        list_trade_fills = self._list_trade_fills
        assert list_trade_fills is not None
        self._trade_generation += 1
        generation = self._trade_generation
        self._latest_trade_filters = filters
        self._pending_trade_filters = None
        self._failed_trade_filters = None
        task = BackgroundTask(lambda: list_trade_fills(filters))
        task.signals.succeeded.connect(
            lambda result: self._trade_task_succeeded(generation, filters, result)
        )
        task.signals.failed.connect(
            lambda error: self._trade_task_failed(generation, filters, error)
        )
        task.signals.finished.connect(lambda: self._trade_task_finished(generation))
        self._trade_tasks[generation] = task
        self.trades_loading.emit(True)
        self._thread_pool.start(task)

    def _trade_task_succeeded(
        self, generation: int, filters: TradeHistoryFilter, result: object
    ) -> None:
        if not self._trade_result_is_current(generation, filters):
            return
        if isinstance(result, tuple) and all(
            isinstance(item, TradeFillHistoryItem) for item in result
        ):
            if not result:
                self.trades_empty.emit()
                return
            self.trades_ready.emit(result)
            return
        self._failed_trade_filters = filters
        self.trades_unavailable.emit("Trade History unavailable")

    def _trade_task_failed(
        self, generation: int, filters: TradeHistoryFilter, error: object
    ) -> None:
        del error
        if not self._trade_result_is_current(generation, filters):
            return
        self._failed_trade_filters = filters
        self.trades_unavailable.emit("Trade History unavailable")

    def _trade_task_finished(self, generation: int) -> None:
        self._trade_tasks.pop(generation, None)
        if self._closed:
            return
        pending = self._pending_trade_filters
        if pending is not None:
            self._start_trade_task(pending)
            return
        if generation == self._trade_generation:
            self.trades_loading.emit(False)

    def _trade_result_is_current(
        self, generation: int, filters: TradeHistoryFilter
    ) -> bool:
        return (
            not self._closed
            and self._active_tab is HistoryTab.TRADES
            and generation == self._trade_generation
            and filters == self._filters
        )

    def _request_basket_history(self) -> None:
        list_basket_history = self._list_basket_history
        if list_basket_history is None or self._closed:
            return
        filters = self._filters
        if self._basket_tasks and filters != self._latest_basket_filters:
            self._basket_generation += 1
            self._latest_basket_filters = filters
            self._pending_basket_filters = filters
            return
        if self._basket_tasks:
            return
        self._start_basket_history_task(filters)

    def _start_basket_history_task(self, filters: TradeHistoryFilter) -> None:
        list_basket_history = self._list_basket_history
        assert list_basket_history is not None
        self._basket_generation += 1
        generation = self._basket_generation
        self._latest_basket_filters = filters
        self._pending_basket_filters = None
        self._failed_basket_filters = None
        task = BackgroundTask(lambda: list_basket_history(filters))
        task.signals.succeeded.connect(
            lambda result: self._basket_task_succeeded(generation, filters, result)
        )
        task.signals.failed.connect(
            lambda error: self._basket_task_failed(generation, filters, error)
        )
        task.signals.finished.connect(lambda: self._basket_task_finished(generation))
        self._basket_tasks[generation] = task
        self._set_baskets_loading(True)
        self._thread_pool.start(task)

    def _basket_task_succeeded(
        self, generation: int, filters: TradeHistoryFilter, result: object
    ) -> None:
        if not self._basket_result_is_current(generation, filters):
            return
        if not self._valid_basket_result(result):
            self._failed_basket_filters = filters
            self.baskets_unavailable.emit("Position History unavailable")
            return
        if result.items:
            self.baskets_ready.emit(result)
        else:
            self.baskets_empty.emit(result)

    def _basket_task_failed(
        self, generation: int, filters: TradeHistoryFilter, error: object
    ) -> None:
        del error
        if not self._basket_result_is_current(generation, filters):
            return
        self._failed_basket_filters = filters
        self.baskets_unavailable.emit("Position History unavailable")

    def _basket_task_finished(self, generation: int) -> None:
        self._basket_tasks.pop(generation, None)
        if self._closed:
            return
        pending = self._pending_basket_filters
        if pending is not None:
            self._start_basket_history_task(pending)
            return
        if generation == self._basket_generation:
            self._set_baskets_loading(False)

    def _basket_result_is_current(
        self, generation: int, filters: TradeHistoryFilter
    ) -> bool:
        return (
            not self._closed
            and self._active_tab is HistoryTab.BASKETS
            and generation == self._basket_generation
            and filters == self._filters
        )

    @staticmethod
    def _valid_basket_result(result: object) -> TypeGuard[BasketHistoryResult]:
        return (
            isinstance(result, BasketHistoryResult)
            and isinstance(result.items, tuple)
            and all(isinstance(item, BasketResult) for item in result.items)
            and isinstance(result.net_realized_pnl, Decimal)
            and result.net_realized_pnl.is_finite()
        )

    @Slot()
    def retry_baskets(self) -> None:
        if self._closed or self._failed_basket_filters != self._filters:
            return
        self._request_basket_history()

    @Slot()
    def retry_fills(self) -> None:
        if (
            self._closed
            or self._active_tab is not HistoryTab.TRADES
            or self._failed_trade_filters != self._filters
        ):
            return
        self._request_trades()

    @Slot()
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._basket_generation += 1
        self._trade_generation += 1
        self._failed_basket_filters = None
        self._failed_trade_filters = None
        self._pending_basket_filters = None
        self._pending_trade_filters = None

    def _set_baskets_loading(self, loading: bool) -> None:
        if self._baskets_are_loading == loading:
            return
        self._baskets_are_loading = loading
        self.baskets_loading.emit(loading)
