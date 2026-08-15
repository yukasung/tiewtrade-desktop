from collections.abc import Sequence
from datetime import date
from typing import Any

from PySide6.QtCore import QDate, QEvent, QObject, QSignalBlocker, Qt, Signal, Slot
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QPushButton,
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
from tiewtrade.ui.display_time import display_today
from tiewtrade.ui.sign_state import paint_by_sign
from tiewtrade.ui.table_column import Column, headers, table_item
from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay
from tiewtrade.ui.trade_history_presenter import (
    BASKET_COLUMNS,
    FILL_COLUMNS,
    QuickRange,
    TradeHistoryFilterValues,
    quick_range_dates,
    usdt_text,
)
from tiewtrade.ui.trade_history_workflow import HistoryTab

# A QDateEdit always holds some date, so the earliest one it accepts stands
# for "no date at all" and is shown as the placeholder instead of a day.
EMPTY_DATE = QDate(1752, 9, 14)

NO_BASKET_HISTORY = "No Position history"

LINE_SPACING = 24
GROUP_SPACING = 6


class OptionalDateEdit(QDateEdit):
    """A date field that may be left empty, where empty means no bound."""

    def __init__(
        self,
        object_name: str,
        accessible_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAccessibleName(accessible_name)
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setMinimumDate(EMPTY_DATE)
        self.setSpecialValueText("YYYY-MM-DD")
        self.setDate(EMPTY_DATE)
        self.setMinimumWidth(120)
        self.setMaximumWidth(150)
        calendar = self.calendarWidget()
        if calendar is not None:
            calendar.installEventFilter(self)

    def clear_date(self) -> None:
        self.setDate(EMPTY_DATE)

    def has_date(self) -> bool:
        return self.date() != EMPTY_DATE

    def optional_date(self) -> date | None:
        """The day picked, or nothing where the field was left empty."""
        if not self.has_date():
            return None
        picked = self.date()
        return date(picked.year(), picked.month(), picked.day())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Deleting the text empties the bound rather than typing over it."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.clear_date()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # An empty field holds 1752, so a calendar opened on one would land two
        # centuries from the month the trader meant to pick.
        if (
            watched is self.calendarWidget()
            and event.type() == QEvent.Type.Show
            and not self.has_date()
        ):
            today = QDate.currentDate()
            self.calendarWidget().setCurrentPage(today.year(), today.month())
        return super().eventFilter(watched, event)


# Which line each group goes on, from the one row Binance shows down to the
# arrangement of last resort. The first one that fits the width is used.
_ARRANGEMENTS = ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 2))


class FilterBar(QFrame):
    """One row of filters, folded onto more lines where the width is short.

    The tables below live in a scroll area with no horizontal bar, so a row
    too wide to fit would be cut off rather than scrolled to. Folding keeps
    every control reachable at the narrowest window the app supports.
    """

    def __init__(
        self,
        groups: tuple[QWidget, ...],
        footer: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("historyFilterBar")
        self._groups = groups
        lines = tuple(_group_line() for _ in groups)
        self._lines = tuple(line for line, _ in lines)
        self._line_layouts = tuple(layout for _, layout in lines)
        self._arrangement: tuple[int, ...] = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)
        for line in self._lines:
            layout.addWidget(line)
        layout.addWidget(footer)
        self._arrange(_ARRANGEMENTS[0])

    def line_of(self, widget: QWidget) -> int:
        """Which line a control ended up on, so intent can be read in a test."""
        for index, line in enumerate(self._lines):
            if line.isAncestorOf(widget):
                return index
        raise ValueError("widget is not in the filter bar")

    def line_count(self) -> int:
        return len(set(self._arrangement))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        layout = self.layout()
        margins = layout.contentsMargins() if layout is not None else None
        available = self.width() - (
            margins.left() + margins.right() if margins is not None else 0
        )
        if available <= 0:
            return
        arrangement = self._fitting(available)
        if arrangement == self._arrangement:
            return
        self._arrange(arrangement)

    def _fitting(self, available: int) -> tuple[int, ...]:
        for arrangement in _ARRANGEMENTS:
            if self._width_of(arrangement) <= available:
                return arrangement
        return _ARRANGEMENTS[-1]

    def _width_of(self, arrangement: tuple[int, ...]) -> int:
        """The widest line this arrangement would need."""
        widest = 0
        for line_index in range(len(self._lines)):
            widths = [
                group.sizeHint().width()
                for group, index in zip(self._groups, arrangement, strict=True)
                if index == line_index
            ]
            if not widths:
                continue
            widest = max(widest, sum(widths) + LINE_SPACING * (len(widths) - 1))
        return widest

    def _arrange(self, arrangement: tuple[int, ...]) -> None:
        for line_layout in self._line_layouts:
            # Everything but the trailing stretch, which holds the groups left.
            while line_layout.count() > 1:
                line_layout.takeAt(0)
        for group, line_index in zip(self._groups, arrangement, strict=True):
            line_layout = self._line_layouts[line_index]
            line_layout.insertWidget(line_layout.count() - 1, group)
        for index, line in enumerate(self._lines):
            line.setVisible(index in arrangement)
        self._arrangement = arrangement


def _group_line() -> tuple[QWidget, QHBoxLayout]:
    """One line of the bar, holding whichever groups fit on it."""
    line = QWidget()
    # A line asks for no width of its own. Were it to ask for the width of
    # everything on it, the bar could never be narrower than the one-line
    # arrangement, and it would be clipped rather than folded.
    line.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    row = QHBoxLayout(line)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(LINE_SPACING)
    row.addStretch()
    return line, row


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


def _group(*widgets: QWidget) -> QWidget:
    """Controls that are read together and so wrap together."""
    group = QWidget()
    row = QHBoxLayout(group)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(GROUP_SPACING)
    for widget in widgets:
        row.addWidget(widget)
    return group


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

        self.range_chips: dict[QuickRange, QPushButton] = {}
        self.range_group = QButtonGroup(self)
        self.range_group.setExclusive(True)
        for kind in QuickRange:
            chip = QPushButton(kind.value)
            chip.setObjectName(f"range{kind.name.title().replace('_', '')}")
            chip.setAccessibleName(f"Last {kind.value}")
            chip.setProperty("rangeChip", True)
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            # Bound now, or every chip would close over the last range.
            chip.clicked.connect(lambda _checked, picked=kind: self._pick_range(picked))
            self.range_group.addButton(chip)
            self.range_chips[kind] = chip

        self.from_date = OptionalDateEdit("fromDate", "From Date")
        self.date_separator = QLabel("to")
        self.date_separator.setObjectName("filterLabel")
        self.to_date = OptionalDateEdit("toDate", "To Date")
        # A date typed by hand is the trader's own range, not one of the chips'.
        self.from_date.dateChanged.connect(self._clear_range_chips)
        self.to_date.dateChanged.connect(self._clear_range_chips)

        self.filter_error = QLabel()
        self.filter_error.setObjectName("filterError")
        self.filter_error.setWordWrap(True)
        self.filter_error.setVisible(False)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("secondaryButton")
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("primaryButton")
        self.reset_button.clicked.connect(self._reset_filters)
        self.search_button.clicked.connect(self._apply_filters)

        self.total_net_pnl_label = QLabel("Total Profit / Loss")
        self.total_net_pnl_label.setObjectName("summaryLabel")
        self.total_net_pnl_label.setVisible(False)
        self.total_net_pnl = QLabel()
        self.total_net_pnl.setObjectName("summaryValue")
        self.total_net_pnl.setAccessibleName("Total Profit / Loss")
        self.total_net_pnl.setVisible(False)
        self.total_items = QLabel()
        self.total_items.setObjectName("supportingText")

        self.basket_table = self._table(
            "basketHistoryTable", "Position History", BASKET_COLUMNS
        )
        self.basket_empty_state = TableEmptyStateOverlay(self.basket_table)
        self.basket_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.basket_state = self._state_label("basketState", "Position History status")
        self.retry_baskets_button = self._retry_button("retryBasketsButton")
        self.retry_baskets_button.clicked.connect(self.baskets_retry_requested)

        self.fill_table = self._table("tradeFillsTable", "Trade Fills", FILL_COLUMNS)
        self.fill_empty_state = TableEmptyStateOverlay(self.fill_table)
        self.fill_state = self._state_label("fillState", "Trade Fills status")
        self.retry_fills_button = self._retry_button("retryFillsButton")
        self.retry_fills_button.clicked.connect(self.fills_retry_requested)
        self.history_stack = QStackedWidget()
        self.history_stack.setObjectName("historyStack")

        self._build_layout()

    @property
    def basket_headers(self) -> tuple[str, ...]:
        return self.BASKET_HEADERS

    @property
    def fill_headers(self) -> tuple[str, ...]:
        return self.FILL_HEADERS

    def filter_values(self) -> TradeHistoryFilterValues:
        return TradeHistoryFilterValues(
            from_date=self.from_date.optional_date(),
            to_date=self.to_date.optional_date(),
        )

    @Slot(bool)
    def set_baskets_loading(self, loading: bool) -> None:
        self._baskets_loading = loading
        self._refresh_filter_controls()
        if not loading:
            return
        if not self._has_basket_result:
            self._clear_basket_result()
        self._set_basket_state("Loading trade history…")
        self.retry_baskets_button.setVisible(False)

    @Slot(object)
    def show_baskets(self, result: BasketHistoryResult) -> None:
        fill_table(self.basket_table, BASKET_COLUMNS, result.items)

        self._has_basket_result = True
        self.total_net_pnl_label.setVisible(True)
        self.total_net_pnl.setText(usdt_text(result.net_realized_pnl))
        paint_by_sign(self.total_net_pnl, result.net_realized_pnl)
        self.total_net_pnl.setVisible(True)
        self.total_items.setText(f"{len(result.items)} total Positions")
        self.retry_baskets_button.setVisible(False)
        if result.items:
            self.basket_empty_state.clear_message()
            self.basket_state.clear()
            self.basket_state.setVisible(False)
        else:
            self._set_basket_state(NO_BASKET_HISTORY)

    @Slot(object)
    def show_baskets_empty(self, result: BasketHistoryResult) -> None:
        self.show_baskets(result)

    @Slot(str)
    def show_baskets_unavailable(self, message: str) -> None:
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
        fill_table(self.fill_table, FILL_COLUMNS, items)
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
        self.from_date.clear_date()
        self.to_date.clear_date()
        self._clear_range_chips()
        self.filter_error.clear()
        self.filter_error.setVisible(False)
        self.reset_requested.emit()

    def _pick_range(self, kind: QuickRange) -> None:
        """A quick range fills both ends and searches on the spot."""
        from_day, to_day = quick_range_dates(kind, display_today())
        self._set_date(self.from_date, from_day)
        self._set_date(self.to_date, to_day)
        self.range_chips[kind].setChecked(True)
        self._apply_filters()

    def _set_date(self, field: OptionalDateEdit, value: date) -> None:
        # Filling the field from a chip must not read as the trader editing it.
        blocker = QSignalBlocker(field)
        field.setDate(QDate(value.year, value.month, value.day))
        del blocker

    @Slot()
    def _clear_range_chips(self) -> None:
        checked = self.range_group.checkedButton()
        if checked is None:
            return
        self.range_group.setExclusive(False)
        checked.setChecked(False)
        self.range_group.setExclusive(True)

    def show_history_tab(self, tab: HistoryTab) -> None:
        """Show the half of the history the workspace's tab strip asked for.

        The two halves share one filter bar, so they stay one widget however
        the tabs above them are arranged; only which table is on top changes.

        This is the only way the shown half changes. A result arriving from a
        query must never switch it: the reader chose the half, and a reply
        that lands a second later has no business taking the wheel back.
        """
        if tab is self._active_history_tab:
            return
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

        self.filter_bar = self._build_filter_card()
        root.addWidget(self.filter_bar)

        basket_heading = QHBoxLayout()
        basket_heading.setSpacing(12)
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

        # Reading order, which the folding above does not disturb: the ranges,
        # then the dates they fill, then the actions, then the table itself.
        focus_chain: tuple[QWidget, ...] = (
            *self.range_chips.values(),
            self.from_date,
            self.to_date,
            self.search_button,
            self.reset_button,
            self.basket_table,
        )
        for earlier, later in zip(focus_chain, focus_chain[1:], strict=False):
            QWidget.setTabOrder(earlier, later)

        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)

    def _build_filter_card(self) -> FilterBar:
        return FilterBar(
            (
                _group(*self.range_chips.values()),
                _group(self.from_date, self.date_separator, self.to_date),
                _group(self.search_button, self.reset_button),
            ),
            self.filter_error,
        )

    def _clear_fill_result(self) -> None:
        self._clear_table(self.fill_table)
        self.fill_empty_state.clear_message()
        self.fill_state.clear()
        self.fill_state.setVisible(False)
        self.retry_fills_button.setVisible(False)

    def _set_filter_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            *self.range_chips.values(),
            self.from_date,
            self.to_date,
            self.reset_button,
            self.search_button,
        ):
            widget.setEnabled(enabled)

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
        if message == NO_BASKET_HISTORY:
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
        # Deliberately not ResizeToContents: that mode re-measures every row
        # of a column on every cell written, which froze the window for
        # seconds on each refresh. `fill_table` measures once instead.
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
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
