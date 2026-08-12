from datetime import date
from typing import Any

from PySide6.QtCore import QDate, QSignalBlocker, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.paper_symbols import PAPER_SYMBOLS
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
)
from tiewtrade.market_data.config import SUPPORTED_V1_TIMEFRAME_CHOICES
from tiewtrade.ui.sign_state import paint_by_sign
from tiewtrade.ui.table_column import Column, headers, table_item
from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay
from tiewtrade.ui.trade_history_presenter import (
    BASKET_COLUMNS,
    FILL_COLUMNS,
    TradeHistoryFilterValues,
    usdt_text,
)
from tiewtrade.ui.trade_history_workflow import HistoryTab


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

        self.symbol = self._combo(
            "historySymbol",
            (("All", None),) + tuple((symbol, symbol) for symbol in PAPER_SYMBOLS),
        )
        self.timeframe = self._combo(
            "historyTimeframe",
            (("All", None),)
            + tuple((value, value) for value in SUPPORTED_V1_TIMEFRAME_CHOICES),
        )
        self.market = self._combo(
            "historyMarket",
            (("All", None), ("Spot", "spot"), ("Futures", "futures")),
        )
        self.mode = self._combo(
            "historyMode",
            (("All", None), ("Paper", "paper"), ("Live", "live")),
        )
        self.status = self._combo(
            "historyStatus",
            (("All", None), ("Open", "open"), ("Closed", "closed")),
        )

        self.from_date_enabled = QCheckBox("From Date")
        self.from_date_enabled.setObjectName("fromDateEnabled")
        self.from_date = self._date_edit("fromDate", "From Date")
        self.to_date_enabled = QCheckBox("To Date")
        self.to_date_enabled.setObjectName("toDateEnabled")
        self.to_date = self._date_edit("toDate", "To Date")
        self.from_date_enabled.toggled.connect(self.from_date.setEnabled)
        self.to_date_enabled.toggled.connect(self.to_date.setEnabled)

        self.filter_error = QLabel()
        self.filter_error.setObjectName("filterError")
        self.filter_error.setWordWrap(True)
        self.filter_error.setVisible(False)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("secondaryButton")
        self.apply_button = QPushButton("Apply Filters")
        self.apply_button.setObjectName("primaryButton")
        self.reset_button.clicked.connect(self._reset_filters)
        self.apply_button.clicked.connect(self._apply_filters)

        self.total_net_pnl_label = QLabel("Total Net PnL")
        self.total_net_pnl_label.setObjectName("summaryLabel")
        self.total_net_pnl_label.setVisible(False)
        self.total_net_pnl = QLabel()
        self.total_net_pnl.setObjectName("summaryValue")
        self.total_net_pnl.setAccessibleName("Total Net PnL")
        self.total_net_pnl.setVisible(False)
        self.total_items = QLabel()
        self.total_items.setObjectName("supportingText")

        self.basket_table = self._table(
            "basketHistoryTable", "Basket History", BASKET_COLUMNS
        )
        self.basket_empty_state = TableEmptyStateOverlay(self.basket_table)
        self.basket_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.basket_state = self._state_label("basketState", "Basket History status")
        self.retry_baskets_button = self._retry_button("retryBasketsButton")
        self.retry_baskets_button.clicked.connect(self.baskets_retry_requested)

        self.fill_table = self._table("tradeFillsTable", "Trade Fills", FILL_COLUMNS)
        self.fill_empty_state = TableEmptyStateOverlay(self.fill_table)
        self.fill_state = self._state_label("fillState", "Trade Fills status")
        self.retry_fills_button = self._retry_button("retryFillsButton")
        self.retry_fills_button.clicked.connect(self.fills_retry_requested)
        self.history_tabs = QTabBar()
        self.history_tabs.setObjectName("historyTabs")
        self.history_tabs.setExpanding(False)
        self.history_tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.history_stack = QStackedWidget()
        self.history_stack.setObjectName("historyStack")
        self.history_tabs.currentChanged.connect(self._history_tab_changed)

        self._build_layout()

    @property
    def basket_headers(self) -> tuple[str, ...]:
        return self.BASKET_HEADERS

    @property
    def fill_headers(self) -> tuple[str, ...]:
        return self.FILL_HEADERS

    def filter_values(self) -> TradeHistoryFilterValues:
        return TradeHistoryFilterValues(
            symbol=self._combo_value(self.symbol),
            timeframe=self._combo_value(self.timeframe),
            market_type=self._combo_value(self.market),
            trade_mode=self._combo_value(self.mode),
            status=self._combo_value(self.status),
            from_date=(
                self._python_date(self.from_date.date())
                if self.from_date_enabled.isChecked()
                else None
            ),
            to_date=(
                self._python_date(self.to_date.date())
                if self.to_date_enabled.isChecked()
                else None
            ),
        )

    @Slot(bool)
    def set_baskets_loading(self, loading: bool) -> None:
        self._baskets_loading = loading
        if loading:
            self._show_basket_tab()
        self._refresh_filter_controls()
        if not loading:
            return
        if not self._has_basket_result:
            self._clear_basket_result()
        self._set_basket_state("Loading trade history…")
        self.retry_baskets_button.setVisible(False)

    @Slot(object)
    def show_baskets(self, result: BasketHistoryResult) -> None:
        self._show_basket_tab()
        blocker = QSignalBlocker(self.basket_table)
        self.basket_table.setRowCount(len(result.items))
        for row_index, basket in enumerate(result.items):
            for column_index, column in enumerate(BASKET_COLUMNS):
                self.basket_table.setItem(
                    row_index, column_index, table_item(column, basket)
                )
        del blocker

        self._has_basket_result = True
        self.total_net_pnl_label.setVisible(True)
        self.total_net_pnl.setText(usdt_text(result.net_realized_pnl))
        paint_by_sign(self.total_net_pnl, result.net_realized_pnl)
        self.total_net_pnl.setVisible(True)
        self.total_items.setText(f"{len(result.items)} total Baskets")
        self.retry_baskets_button.setVisible(False)
        if result.items:
            self.basket_empty_state.clear_message()
            self.basket_state.clear()
            self.basket_state.setVisible(False)
        else:
            self._set_basket_state("No Basket history")

    @Slot(object)
    def show_baskets_empty(self, result: BasketHistoryResult) -> None:
        self.show_baskets(result)

    @Slot(str)
    def show_baskets_unavailable(self, message: str) -> None:
        self._show_basket_tab()
        if self._has_basket_result:
            self._set_basket_state(f"Stale · {message}")
        else:
            self._clear_basket_result()
            self._set_basket_state(message)
        self.retry_baskets_button.setVisible(True)

    @Slot(str)
    def show_filter_error(self, message: str) -> None:
        self.filter_error.setText(message)
        self.filter_error.setVisible(True)

    @Slot(object)
    def show_trades(self, items: tuple[TradeFillHistoryItem, ...]) -> None:
        self._fills_loading = False
        blocker = QSignalBlocker(self.fill_table)
        self.fill_table.setRowCount(len(items))
        for row_index, item in enumerate(items):
            for column_index, column in enumerate(FILL_COLUMNS):
                self.fill_table.setItem(
                    row_index, column_index, table_item(column, item)
                )
        del blocker
        self.fill_empty_state.clear_message()
        self.fill_state.clear()
        self.fill_state.setVisible(False)
        self.retry_fills_button.setVisible(False)

    @Slot(bool)
    def set_trades_loading(self, loading: bool) -> None:
        self._fills_loading = loading
        self._refresh_filter_controls()
        if not loading:
            return
        self._set_fill_state("Loading trade history…")
        self.retry_fills_button.setVisible(False)

    @Slot()
    def show_trades_empty(self) -> None:
        self._fills_loading = False
        self._clear_fill_result()
        self._set_fill_state("No trade history")

    @Slot(str)
    def show_trades_unavailable(self, message: str) -> None:
        self._fills_loading = False
        if self.fill_table.rowCount() > 0:
            self._set_fill_state(f"Stale · {message}")
        else:
            self._clear_fill_result()
            self._set_fill_state(message)
        self.retry_fills_button.setVisible(True)

    @Slot()
    def _apply_filters(self) -> None:
        self.filter_error.clear()
        self.filter_error.setVisible(False)
        self.apply_filters_requested.emit(self.filter_values())

    @Slot()
    def _reset_filters(self) -> None:
        for combo in (
            self.symbol,
            self.timeframe,
            self.market,
            self.mode,
            self.status,
        ):
            combo.setCurrentIndex(0)
        self.from_date_enabled.setChecked(False)
        self.to_date_enabled.setChecked(False)
        self.filter_error.clear()
        self.filter_error.setVisible(False)
        self.reset_requested.emit()

    @Slot(int)
    def _history_tab_changed(self, index: int) -> None:
        self.history_stack.setCurrentIndex(index)
        tab = HistoryTab.BASKETS if index == 0 else HistoryTab.TRADES
        self._active_history_tab = tab
        self.status.setVisible(tab is HistoryTab.BASKETS)
        if hasattr(self, "status_label"):
            self.status_label.setVisible(tab is HistoryTab.BASKETS)
        self._refresh_filter_controls()
        self.history_tab_changed.emit(tab)

    def _show_trade_tab(self) -> None:
        self.history_tabs.setCurrentIndex(1)

    def _show_basket_tab(self) -> None:
        self.history_tabs.setCurrentIndex(0)

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

        self.history_tabs.addTab("Basket History")
        self.history_tabs.addTab("Trade History")
        root.addWidget(self.history_tabs)
        root.addWidget(self._build_filter_card())

        basket_heading = QHBoxLayout()
        basket_heading.setSpacing(12)
        basket_title = QLabel("Basket History")
        basket_title.setObjectName("sectionTitle")
        basket_heading.addWidget(basket_title)
        basket_heading.addWidget(self.total_items)
        basket_heading.addStretch()
        basket_heading.addWidget(self.total_net_pnl_label)
        basket_heading.addWidget(self.total_net_pnl)
        basket_page = QWidget()
        basket_layout = QVBoxLayout(basket_page)
        basket_layout.setContentsMargins(0, 0, 0, 0)
        basket_layout.setSpacing(10)
        basket_layout.addLayout(basket_heading)
        basket_layout.addWidget(self.basket_table, 1)
        basket_layout.addLayout(
            self._state_row(self.basket_state, self.retry_baskets_button)
        )

        trade_page = QWidget()
        trade_layout = QVBoxLayout(trade_page)
        trade_layout.setContentsMargins(0, 0, 0, 0)
        trade_layout.setSpacing(10)
        trade_layout.addWidget(self.fill_table, 1)
        trade_layout.addLayout(
            self._state_row(self.fill_state, self.retry_fills_button)
        )

        self.history_stack.addWidget(basket_page)
        self.history_stack.addWidget(trade_page)
        root.addWidget(self.history_stack, 1)

        QWidget.setTabOrder(self.apply_button, self.history_tabs)
        QWidget.setTabOrder(self.history_tabs, self.basket_table)

        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)

    def _build_filter_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("historyFilterBar")
        layout = QGridLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(8)

        for column, (label, field) in enumerate(
            (
                ("Symbol", self.symbol),
                ("Timeframe", self.timeframe),
                ("Market", self.market),
                ("Mode", self.mode),
                ("Status", self.status),
            )
        ):
            field_label = self._field_label(label, field)
            if field is self.status:
                self.status_label = field_label
            layout.addWidget(field_label, 0, column)
            layout.addWidget(field, 1, column)

        layout.addWidget(self.from_date_enabled, 2, 0)
        layout.addWidget(self.from_date, 3, 0, 1, 2)
        layout.addWidget(self.to_date_enabled, 2, 2)
        layout.addWidget(self.to_date, 3, 2, 1, 2)
        actions = QHBoxLayout()
        actions.addWidget(self.reset_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions, 3, 4)
        layout.addWidget(self.filter_error, 4, 0, 1, 5)
        layout.setColumnStretch(5, 1)
        return card

    def _clear_fill_result(self) -> None:
        self._clear_table(self.fill_table)
        self.fill_empty_state.clear_message()
        self.fill_state.clear()
        self.fill_state.setVisible(False)
        self.retry_fills_button.setVisible(False)

    def _set_filter_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.symbol,
            self.timeframe,
            self.market,
            self.mode,
            self.status,
            self.from_date_enabled,
            self.to_date_enabled,
            self.reset_button,
            self.apply_button,
        ):
            widget.setEnabled(enabled)
        self.from_date.setEnabled(enabled and self.from_date_enabled.isChecked())
        self.to_date.setEnabled(enabled and self.to_date_enabled.isChecked())

    def _refresh_filter_controls(self) -> None:
        """Only the tab being read can hold the filters, since only it is loading."""
        if self._active_history_tab is HistoryTab.BASKETS:
            active_loading = self._baskets_loading
        else:
            active_loading = self._fills_loading
        self._set_filter_controls_enabled(not active_loading)

    def _set_basket_state(self, message: str) -> None:
        """Nothing found reads inside the empty table; anything else reads below it."""
        self.basket_state.setText(message)
        if message == "No Basket history":
            self.basket_empty_state.show_message(message)
            self.basket_state.setVisible(False)
            return
        self.basket_empty_state.clear_message()
        self.basket_state.setVisible(True)

    def _set_fill_state(self, message: str) -> None:
        self.fill_state.setText(message)
        if message == "No trade history":
            self.fill_empty_state.show_message(message)
            self.fill_state.setVisible(False)
            return
        self.fill_empty_state.clear_message()
        self.fill_state.setVisible(True)

    @staticmethod
    def _combo(
        object_name: str, choices: tuple[tuple[str, str | None], ...]
    ) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(object_name)
        for label, value in choices:
            combo.addItem(label, value)
        combo.setMinimumWidth(140)
        combo.setMaximumWidth(190)
        return combo

    @staticmethod
    def _date_edit(object_name: str, accessible_name: str) -> QDateEdit:
        field = QDateEdit(QDate.currentDate())
        field.setObjectName(object_name)
        field.setAccessibleName(accessible_name)
        field.setCalendarPopup(True)
        field.setDisplayFormat("yyyy-MM-dd")
        field.setEnabled(False)
        field.setMinimumWidth(190)
        field.setMaximumWidth(240)
        return field

    @staticmethod
    def _table(
        object_name: str,
        accessible_name: str,
        columns: tuple[Column[Any], ...],
    ) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setObjectName(object_name)
        table.setAccessibleName(accessible_name)
        table.setHorizontalHeaderLabels(list(headers(columns)))
        # A header that does not follow its column reads as a different column.
        for index, column in enumerate(columns):
            header_item = table.horizontalHeaderItem(index)
            if header_item is not None:
                header_item.setTextAlignment(column.alignment)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _state_label(object_name: str, accessible_name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setAccessibleName(accessible_name)
        label.setProperty("stateMessage", True)
        label.setWordWrap(True)
        label.setVisible(False)
        return label

    @staticmethod
    def _retry_button(object_name: str) -> QPushButton:
        button = QPushButton("Try Again")
        button.setObjectName(object_name)
        button.setProperty("retryButton", True)
        button.setVisible(False)
        return button

    @staticmethod
    def _state_row(message: QLabel, retry: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(message)
        row.addStretch()
        row.addWidget(retry)
        return row

    @staticmethod
    def _field_label(text: str, field: QWidget) -> QLabel:
        label = QLabel(text)
        label.setObjectName("filterLabel")
        label.setBuddy(field)
        return label

    @staticmethod
    def _combo_value(combo: QComboBox) -> str | None:
        value = combo.currentData()
        return None if value is None else str(value)

    @staticmethod
    def _python_date(value: QDate) -> date:
        return date(value.year(), value.month(), value.day())

    @staticmethod
    def _clear_table(table: QTableWidget) -> None:
        blocker = QSignalBlocker(table)
        table.clearSelection()
        table.setCurrentCell(-1, -1)
        table.setRowCount(0)
        del blocker

    def _clear_basket_result(self) -> None:
        self._has_basket_result = False
        self._clear_table(self.basket_table)
        self.total_net_pnl_label.setVisible(False)
        self.total_net_pnl.clear()
        self.total_net_pnl.setVisible(False)
        self.total_items.clear()
