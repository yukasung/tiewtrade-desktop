"""The range one half of a durable record is read over, and how it is picked.

Trade History and Notifications both read a record over a range of days, so
this bar — quick-range chips, a From/To pair, Search/Reset — is shared rather
than kept once per screen. Each screen that reads more than one record (a
Basket half and a Fill half, say) still owns one bar per half: each half asks
its own question, so each owns the range it is read over.
"""

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
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.ui.date_range_filter import (
    DEFAULT_QUICK_RANGE,
    DateRangeValues,
    QuickRange,
    quick_range_dates,
)
from tiewtrade.ui.display_time import display_today
from tiewtrade.ui.sign_state import render_realized_total

# A QDateEdit always holds some date, so the earliest one it accepts stands
# for "no date at all" and is shown as the placeholder instead of a day.
EMPTY_DATE = QDate(1752, 9, 14)

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

    def filter_values(self) -> DateRangeValues:
        return DateRangeValues(
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
