from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, QEvent, QObject, QSignalBlocker, Qt, Signal, Slot
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QFrame,
    QHBoxLayout,
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
from tiewtrade.ui.history_section import HistorySection
from tiewtrade.ui.sign_state import render_realized_total
from tiewtrade.ui.table_column import Column, headers, table_item
from tiewtrade.ui.trade_history_presenter import (
    BASKET_COLUMNS,
    DEFAULT_QUICK_RANGE,
    FILL_COLUMNS,
    QuickRange,
    TradeHistoryFilterValues,
    quick_range_dates,
)
from tiewtrade.ui.trade_history_workflow import HistoryTab

# A QDateEdit always holds some date, so the earliest one it accepts stands
# for "no date at all" and is shown as the placeholder instead of a day.
EMPTY_DATE = QDate(1752, 9, 14)

NO_BASKET_HISTORY = "No Position history"
NO_TRADE_HISTORY = "No trade history"

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


def _group(*widgets: QWidget, stretch_before: QWidget | None = None) -> QWidget:
    """Controls that are read together and so wrap together.

    A stretch sits just before `stretch_before`, holding it and everything
    after it at the group's own right edge, and the group is asked to expand
    so that stretch has room to hold them against — the line it lands on is
    what actually gives it that room, in `_arrange`. Without a
    `stretch_before` the group asks for no more than its controls need.
    """
    group = QWidget()
    row = QHBoxLayout(group)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(GROUP_SPACING)
    for widget in widgets:
        if widget is stretch_before:
            row.addStretch()
        row.addWidget(widget)
    if stretch_before is not None:
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    return group


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


class FilterBar(QFrame):
    """The range one half of the history is read over, and how it is picked.

    Each half of the history asks its own question, so each half owns one of
    these rather than sharing. The controls fold onto more lines where the
    width is short: the tables live in a scroll area with no horizontal bar,
    so a row too wide to fit would be cut off rather than scrolled to.
    """

    apply_filters_requested = Signal(object)
    reset_requested = Signal()

    apply_filters_requested = Signal(object)
    reset_requested = Signal()

    def __init__(
        self, *, show_total: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("historyFilterBar")

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

        # Read against the same range Search and Reset just picked, so it
        # sits right after them rather than off on its own.
        self.total_label: QLabel | None = None
        button_group_widgets: tuple[QWidget, ...] = (
            self.search_button,
            self.reset_button,
        )
        total_caption: QLabel | None = None
        if show_total:
            total_caption = QLabel("Total P/L")
            total_caption.setObjectName("summaryLabel")
            self.total_label = QLabel()
            self.total_label.setObjectName("basketTotalPnl")
            self.total_label.setAccessibleName("Total P/L")
            button_group_widgets = (
                *button_group_widgets,
                total_caption,
                self.total_label,
            )

        self._groups = (
            _group(*self.range_chips.values()),
            _group(self.from_date, self.date_separator, self.to_date),
            _group(*button_group_widgets, stretch_before=total_caption),
        )
        # How much of its line each group may take: only the one holding the
        # total to the right edge asks for more than its own controls need.
        self._group_stretches = (0, 0, 1 if show_total else 0)
        lines = tuple(_group_line() for _ in self._groups)
        self._lines = tuple(line for line, _ in lines)
        self._line_layouts = tuple(layout for _, layout in lines)
        self._arrangement: tuple[int, ...] = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)
        for line in self._lines:
            layout.addWidget(line)
        layout.addWidget(self.filter_error)
        self._arrange(_ARRANGEMENTS[0])
        self._show_default_range()

    def filter_values(self) -> TradeHistoryFilterValues:
        return TradeHistoryFilterValues(
            from_date=self.from_date.optional_date(),
            to_date=self.to_date.optional_date(),
        )

    def set_total(self, value: Decimal | None) -> None:
        """The realised total for the range currently filtered to, or
        nothing before a result for it has come back.
        """
        if self.total_label is None:
            return
        render_realized_total(self.total_label, value)

    def set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            *self.range_chips.values(),
            self.from_date,
            self.to_date,
            self.reset_button,
            self.search_button,
        ):
            widget.setEnabled(enabled)

    def show_error(self, message: str) -> None:
        self.filter_error.setText(message)
        self.filter_error.setVisible(True)

    def clear_error(self) -> None:
        self.filter_error.clear()
        self.filter_error.setVisible(False)

    def focus_chain(self) -> tuple[QWidget, ...]:
        """Reading order, which the folding does not disturb."""
        return (
            *self.range_chips.values(),
            self.from_date,
            self.to_date,
            self.search_button,
            self.reset_button,
        )

    @Slot()
    def _apply_filters(self) -> None:
        self.clear_error()
        self.apply_filters_requested.emit(self.filter_values())

    @Slot()
    def _reset_filters(self) -> None:
        self._show_default_range()
        self.clear_error()
        self.reset_requested.emit()

    def _show_default_range(self) -> None:
        """Put the bar back to what it opens on, without asking for a read.

        The workflow reads on its own, both when it starts and when it is
        reset, so searching from here would draw the tables a second time.
        """
        from_day, to_day = quick_range_dates(DEFAULT_QUICK_RANGE, display_today())
        self._set_date(self.from_date, from_day)
        self._set_date(self.to_date, to_day)
        self.range_chips[DEFAULT_QUICK_RANGE].setChecked(True)

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
        for group, stretch, line_index in zip(
            self._groups, self._group_stretches, arrangement, strict=True
        ):
            line_layout = self._line_layouts[line_index]
            line_layout.insertWidget(line_layout.count() - 1, group, stretch)
        for index, line in enumerate(self._lines):
            line.setVisible(index in arrangement)
        self._arrangement = arrangement


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
