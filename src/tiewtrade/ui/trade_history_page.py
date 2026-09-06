from collections.abc import Sequence

from PySide6.QtCore import QSignalBlocker, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
)
from tiewtrade.ui.date_range_filter_bar import FilterBar
from tiewtrade.ui.history_section import HistorySection
from tiewtrade.ui.table_column import Column, headers, table_item
from tiewtrade.ui.trade_history_presenter import BASKET_COLUMNS, FILL_COLUMNS
from tiewtrade.ui.trade_history_workflow import HistoryTab

NO_BASKET_HISTORY = "No Position history"
NO_TRADE_HISTORY = "No trade history"


def fill_table[Record](
    table: QTableWidget,
    columns: tuple[Column[Record], ...],
    records: Sequence[Record],
) -> None:
    """Put a whole result into a table in one pass.

    The header must not measure while the cells are written. Under
    ResizeToContents every setItem re-reads every row of that column, so one
    refresh of a few hundred rows costs seconds on the thread that draws the
    app — the window simply stops responding. The width is measured once,
    after all the rows are in, which reads the same and costs one pass.

    Rows are dropped before they are rebuilt for the same reason: replacing
    an item in place is the case that triggers the re-measure.
    """
    table.setUpdatesEnabled(False)
    blocker = QSignalBlocker(table)
    table.setRowCount(0)
    table.setRowCount(len(records))
    for row_index, record in enumerate(records):
        for column_index, column in enumerate(columns):
            table.setItem(row_index, column_index, table_item(column, record))
    del blocker
    table.resizeColumnsToContents()
    table.setUpdatesEnabled(True)


class TradeHistoryPage(QWidget):
    apply_filters_requested = Signal(object)
    reset_requested = Signal()
    baskets_retry_requested = Signal()
    fills_retry_requested = Signal()
    history_tab_changed = Signal(object)

    BASKET_HEADERS = headers(BASKET_COLUMNS)
    FILL_HEADERS = headers(FILL_COLUMNS)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tradeHistoryPage")
        self.setAccessibleName("Trade History")
        self._has_basket_result = False
        self._baskets_loading = False
        self._fills_loading = False
        self._active_history_tab = HistoryTab.BASKETS

        # Each half asks its own question of the record, so each owns the
        # range it is read over. Only the visible bar can be clicked, and the
        # visible bar is always the active half, so the workflow can apply
        # what it is handed to the half it is already showing.
        self.basket_filters = FilterBar(show_total=True)
        self.trade_filters = FilterBar()
        for bar in (self.basket_filters, self.trade_filters):
            bar.apply_filters_requested.connect(self.apply_filters_requested)
            bar.reset_requested.connect(self.reset_requested)

        self.baskets = HistorySection(
            filters=self.basket_filters,
            columns=BASKET_COLUMNS,
            table_object_name="basketHistoryTable",
            state_object_name="basketState",
            retry_object_name="retryBasketsButton",
            accessible_name="Position History",
            empty_message=NO_BASKET_HISTORY,
            # A round is read, not picked: there is nothing to do with one.
            selectable=False,
        )
        self.baskets.retry_requested.connect(self.baskets_retry_requested)
        self.fills = HistorySection(
            filters=self.trade_filters,
            columns=FILL_COLUMNS,
            table_object_name="tradeFillsTable",
            state_object_name="fillState",
            retry_object_name="retryFillsButton",
            accessible_name="Trade Fills",
            empty_message=NO_TRADE_HISTORY,
        )
        self.fills.retry_requested.connect(self.fills_retry_requested)

        # The halves are reached by name all over the window and its tests,
        # which read better than the section they now live on.
        self.basket_table = self.baskets.table
        self.basket_empty_state = self.baskets.empty_state
        self.basket_state = self.baskets.state
        self.retry_baskets_button = self.baskets.retry_button
        # Always built: basket_filters above is constructed with show_total.
        assert self.basket_filters.total_label is not None
        self.basket_total = self.basket_filters.total_label
        self.fill_table = self.fills.table
        self.fill_empty_state = self.fills.empty_state
        self.fill_state = self.fills.state
        self.retry_fills_button = self.fills.retry_button

        self.history_stack = QStackedWidget()
        self.history_stack.setObjectName("historyStack")

        self._build_layout()

    @property
    def basket_headers(self) -> tuple[str, ...]:
        return self.BASKET_HEADERS

    @property
    def fill_headers(self) -> tuple[str, ...]:
        return self.FILL_HEADERS

    @Slot(bool)
    def set_baskets_loading(self, loading: bool) -> None:
        self._baskets_loading = loading
        self._refresh_filter_controls()
        if not loading:
            return
        if not self._has_basket_result:
            self._clear_basket_result()
        self.baskets.show_state("Loading trade history…")
        self.retry_baskets_button.setVisible(False)

    @Slot(object)
    def show_baskets(self, result: BasketHistoryResult) -> None:
        fill_table(self.basket_table, BASKET_COLUMNS, result.items)
        self.basket_filters.set_total(result.realized_pnl)

        self._has_basket_result = True
        self.retry_baskets_button.setVisible(False)
        if result.items:
            self.baskets.clear_state()
        else:
            self.baskets.show_state(NO_BASKET_HISTORY)

    @Slot(object)
    def show_baskets_empty(self, result: BasketHistoryResult) -> None:
        self.show_baskets(result)

    @Slot(str)
    def show_baskets_unavailable(self, message: str) -> None:
        if self._has_basket_result:
            self.baskets.show_state(f"Stale · {message}")
        else:
            self._clear_basket_result()
            self.baskets.show_state(message)
        self.retry_baskets_button.setVisible(True)

    @Slot(str)
    def show_filter_error(self, message: str) -> None:
        self._active_filters().show_error(message)

    @Slot(object)
    def show_trades(self, items: tuple[TradeFillHistoryItem, ...]) -> None:
        self._fills_loading = False
        fill_table(self.fill_table, FILL_COLUMNS, items)
        self.fills.clear_state()

    @Slot(bool)
    def set_trades_loading(self, loading: bool) -> None:
        self._fills_loading = loading
        self._refresh_filter_controls()
        if not loading:
            return
        self.fills.show_state("Loading trade history…")
        self.retry_fills_button.setVisible(False)

    @Slot()
    def show_trades_empty(self) -> None:
        self._fills_loading = False
        self._clear_fill_result()
        self.fills.show_state(NO_TRADE_HISTORY)

    @Slot(str)
    def show_trades_unavailable(self, message: str) -> None:
        self._fills_loading = False
        if self.fill_table.rowCount() > 0:
            self.fills.show_state(f"Stale · {message}")
        else:
            self._clear_fill_result()
            self.fills.show_state(message)
        self.retry_fills_button.setVisible(True)

    def show_history_tab(self, tab: HistoryTab) -> None:
        """Show the half of the history the workspace's tab strip asked for.

        Each half carries its own filter, so switching does not disturb the
        range the other half was being read over.

        This is the only way the shown half changes. A result arriving from a
        query must never switch it: the reader chose the half, and a reply
        that lands a second later has no business taking the wheel back.

        Asking for the half already shown is a reopening, not a no-op: the
        reader came back to see what has landed since, so it is announced
        again and read again.
        """
        self.history_stack.setCurrentIndex(0 if tab is HistoryTab.BASKETS else 1)
        self._active_history_tab = tab
        self._refresh_filter_controls()
        self.history_tab_changed.emit(tab)

    def _build_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("tradeHistoryScroll")
        self.scroll_area.viewport().setObjectName("tradeHistoryViewport")
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("tradeHistoryContent")
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        root = QVBoxLayout(content)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.history_stack.addWidget(self.baskets)
        self.history_stack.addWidget(self.fills)
        root.addWidget(self.history_stack, 1)

        # Each half reads its own filter and then its own table.
        for section in (self.baskets, self.fills):
            chain = section.focus_chain()
            for earlier, later in zip(chain, chain[1:], strict=False):
                QWidget.setTabOrder(earlier, later)

        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)

    def _clear_fill_result(self) -> None:
        self.fills.clear_rows()
        self.fills.clear_state()

    def _refresh_filter_controls(self) -> None:
        """Each half's filter is held only while that half is reading."""
        self.basket_filters.set_controls_enabled(not self._baskets_loading)
        self.trade_filters.set_controls_enabled(not self._fills_loading)

    def _active_filters(self) -> FilterBar:
        if self._active_history_tab is HistoryTab.BASKETS:
            return self.basket_filters
        return self.trade_filters

    def _clear_basket_result(self) -> None:
        self._has_basket_result = False
        self.baskets.clear_rows()
        self.basket_filters.set_total(None)
