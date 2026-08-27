from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

# The gain/loss pair the whole app reads by colour: chart candles, fill badges
# and any figure that can go either way.
POSITIVE_COLOR = "#0ECB81"
NEGATIVE_COLOR = "#F6465D"

DARK_THEME = """
QMainWindow, QWidget#workspace, QWidget#workspaceContent {
    background: #0B0E11;
    color: #F1F5F9;
    font-family: "Inter", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 14px;
}
QFrame#botControl, QFrame#card,
QFrame#statusCard, QFrame#filterCard, QFrame#emptyPanel,
QFrame#workspaceToolbar {
    background: #141A22;
    border: 1px solid #2B3441;
    border-radius: 8px;
}
QFrame#historyFilterBar {
    background: #10151C;
    border-top: 1px solid #2B3441;
    border-bottom: 1px solid #2B3441;
}
QLabel#toolbarTitle {
    color: #F1F5F9;
    font-size: 16px;
    font-weight: 700;
}
QWidget#candlestickChart {
    background: #10151C;
    border: 1px solid #2B3441;
    border-radius: 8px;
}
QLabel {
    color: #F1F5F9;
}
QLabel#pageTitle,
QLabel#detailValue, QLabel#readOnlyValue, QLabel#stateValue {
    color: #F1F5F9;
}
QLabel#pageTitle {
    font-size: 24px;
    font-weight: 700;
}
QLabel#supportingText, QLabel#detailLabel, QLabel#filterLabel,
QLabel#summaryLabel, QLabel#pageLabel, QLabel[stateMessage="true"],
QLabel#positionBasketState, QLabel#basketState, QLabel#fillState {
    color: #94A3B8;
}
QLabel#eyebrow {
    color: #94A3B8;
    font-size: 11px;
    font-weight: 700;
}
QLabel#summaryValue {
    color: #F1F5F9;
    font-weight: 700;
}
QLabel#basketTotalPnl {
    font-weight: 700;
}
QWidget[state="warning"] {
    background: #2A2412;
    color: #F0B90B;
    border-radius: 6px;
}
QFrame#connectionNoticeCard {
    background: #2A2412;
    border: 1px solid #6B5A1B;
    border-radius: 8px;
}
QFrame#connectionNoticeCard QLabel {
    color: #F0B90B;
}
QWidget[state="positive"] { color: #0ECB81; }
QWidget[state="negative"] { color: #F6465D; }
QLineEdit, QComboBox, QSpinBox, QDateEdit {
    background: #1B2430;
    border: 1px solid #3A4656;
    border-radius: 6px;
    color: #F1F5F9;
    min-height: 36px;
    padding: 0 10px;
    selection-background-color: #315CF4;
}
QComboBox#chartSymbol, QComboBox#chartTimeframe {
    padding: 0 40px 0 14px;
}
/* The history filter is one row above the tables, so it keeps its own
   height rather than the taller one a form field is given. */
QDateEdit#fromDate, QDateEdit#toDate {
    min-height: 32px;
    padding: 0 8px;
}
QComboBox#chartSymbol QAbstractItemView::item,
QComboBox#chartTimeframe QAbstractItemView::item {
    padding: 6px 12px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus,
QPushButton:focus {
    border: 2px solid #4C7DFF;
}
QTableWidget:focus {
    border: 0;
    outline: 0;
}
QLineEdit:read-only, QComboBox:disabled, QDateEdit:disabled {
    background: #151C25;
    color: #94A3B8;
}
QComboBox QAbstractItemView {
    alternate-background-color: #141A22;
    background: #1B2430;
    border: 1px solid #3A4656;
    color: #F1F5F9;
    outline: 0;
    selection-background-color: #315CF4;
    selection-color: #F8FAFC;
}
QCalendarWidget {
    background: #141A22;
    color: #F1F5F9;
}
QCalendarWidget QWidget {
    background: #141A22;
    color: #F1F5F9;
}
QCalendarWidget QAbstractItemView {
    alternate-background-color: #141A22;
    background: #141A22;
    color: #F1F5F9;
    selection-background-color: #315CF4;
    selection-color: #F8FAFC;
}
QCalendarWidget QToolButton, QCalendarWidget QMenu {
    background: #1B2430;
    color: #F1F5F9;
}
QPushButton {
    background: #1B2430;
    border: 1px solid #3A4656;
    border-radius: 6px;
    color: #F1F5F9;
    min-height: 36px;
    padding: 0 14px;
}
QPushButton:hover {
    background: #243044;
    border-color: #4C7DFF;
}
QPushButton:disabled {
    background: #151C25;
    border-color: #2B3441;
    color: #6F7D93;
}
QPushButton#primaryButton {
    background: #315CF4;
    border: 0;
    border-radius: 6px;
    color: #F8FAFC;
    font-weight: 650;
    min-height: 38px;
    padding: 0 18px;
}
QPushButton#primaryButton:hover { background: #416DFF; }
QPushButton#primaryButton:disabled {
    background: #26334D;
    color: #6F7D93;
}
QPushButton#secondaryButton, QPushButton[retryButton="true"] {
    background: #1B2430;
    border: 1px solid #3A4656;
    border-radius: 6px;
    color: #F1F5F9;
    min-height: 36px;
    padding: 0 14px;
}
QPushButton#secondaryButton:hover, QPushButton[retryButton="true"]:hover {
    background: #243044;
    border-color: #4C7DFF;
}
QPushButton#secondaryButton:focus, QPushButton[retryButton="true"]:focus {
    border: 2px solid #4C7DFF;
}
/* A quick range reads as a chip: quiet until it is the one in force. */
QPushButton[rangeChip="true"] {
    background: transparent;
    border: 1px solid #3A4656;
    border-radius: 6px;
    color: #CBD5E1;
    font-weight: 600;
    min-height: 30px;
    padding: 0 12px;
}
QPushButton[rangeChip="true"]:hover {
    border-color: #4C7DFF;
    color: #F1F5F9;
}
QPushButton[rangeChip="true"]:focus {
    border: 2px solid #4C7DFF;
}
QPushButton[rangeChip="true"]:checked {
    background: #22355F;
    border-color: #4C7DFF;
    color: #F8FAFC;
}
QPushButton[rangeChip="true"]:disabled {
    border-color: #2B3441;
    color: #6F7D93;
}
/* The order a row can send sits inside the row, so it is sized to the row
   rather than to a form's button, and it reads as the sale it is. */
QPushButton#positionEntryCloseButton {
    background: transparent;
    border: 1px solid #3A4656;
    border-radius: 4px;
    color: #E2E8F0;
    font-size: 11px;
    font-weight: 600;
    min-height: 0;
    padding: 0 10px;
}
QPushButton#positionEntryCloseButton:hover {
    border-color: #F6465D;
    color: #F6465D;
}
QPushButton#positionEntryCloseButton:focus {
    border: 2px solid #4C7DFF;
}
QPushButton#positionEntryCloseButton:disabled {
    border-color: #2B3441;
    color: #6F7D93;
}
QPushButton#destructiveButton {
    background: #F6465D;
    border: 0;
    border-radius: 6px;
    color: #FFFFFF;
    min-height: 38px;
    padding: 0 18px;
}
/* The tab strip and the page under it are two widgets rather than one
   QTabWidget, so the pane around both draws the surface they share. No
   border between them: a tab that names the table under it should not be
   fenced off from it. */
QWidget#workspaceTabbedPane {
    background: #141A22;
}
QStackedWidget#workspaceTabPages {
    background: transparent;
    border: 0;
}
QTabBar::tab {
    background: #0B0E11;
    color: #94A3B8;
    min-height: 36px;
    padding: 0 16px;
}
/* The selected tab is named by its colour and its weight, not by a rule
   under it. A border costs height on every tab to mark one of them, and
   the height above the table is what the table is short of. Weight is
   here so the mark does not rest on colour alone. */
QTabBar::tab:selected {
    color: #6E97FF;
    font-weight: 700;
}
QTabBar#workspaceTabs::tab {
    background: transparent;
    border-radius: 4px;
    min-width: 132px;
    min-height: 28px;
    padding: 0 18px;
}
/* A tab is bare words with no box, so there is nothing to draw a focus
   ring around without inventing a rectangle. Focus tints the tab instead.
   Keyboard users still have to be able to see where they are, and the ID
   in the selector is what lets this beat the transparent rule above. */
QTabBar#workspaceTabs::tab:focus {
    background: #1B2432;
}
QScrollArea#botControlScroll, QWidget#botControlViewport,
QStackedWidget#botControlPages, QScrollArea#tradeHistoryScroll,
QWidget#tradeHistoryViewport, QWidget#tradeHistoryContent {
    background: #141A22;
    border: 0;
}
QScrollArea#botControlScroll QScrollBar:vertical,
QScrollArea#tradeHistoryScroll QScrollBar:vertical {
    background: #0B0E11;
    border: 0;
    margin: 0;
    width: 12px;
}
QScrollArea#botControlScroll QScrollBar::handle:vertical,
QScrollArea#tradeHistoryScroll QScrollBar::handle:vertical {
    background: #3A4656;
    border: 0;
    border-radius: 6px;
    min-height: 24px;
}
QScrollArea#botControlScroll QScrollBar::add-line:vertical,
QScrollArea#botControlScroll QScrollBar::sub-line:vertical,
QScrollArea#tradeHistoryScroll QScrollBar::add-line:vertical,
QScrollArea#tradeHistoryScroll QScrollBar::sub-line:vertical {
    background: #0B0E11;
    border: 0;
    height: 0;
}
QScrollArea#botControlScroll QScrollBar::add-page:vertical,
QScrollArea#botControlScroll QScrollBar::sub-page:vertical,
QScrollArea#tradeHistoryScroll QScrollBar::add-page:vertical,
QScrollArea#tradeHistoryScroll QScrollBar::sub-page:vertical {
    background: #0B0E11;
}
QTableWidget {
    alternate-background-color: #111821;
    background: #141A22;
    border: 0;
    color: #F1F5F9;
    gridline-color: #2B3441;
    selection-background-color: #22355F;
    selection-color: #F8FAFC;
}
QTableWidget::item { padding: 7px 8px; }
QTableWidget::item:focus { outline: 0; }
/* The strip past the last column is the header widget itself, not a
   section, and it is on show whenever the columns do not fill the width. */
QHeaderView {
    background: #1B2430;
    border: 0;
    border-bottom: 1px solid #2B3441;
}
QHeaderView::section {
    background: #1B2430;
    border: 0;
    border-bottom: 1px solid #2B3441;
    color: #CBD5E1;
    font-weight: 650;
    padding: 8px;
}
QLabel#fieldError, QLabel#filterError { color: #F6465D; }
QLabel#unavailableMessage {
    background: #2A1B20;
    border: 1px solid #71313D;
    border-radius: 6px;
    color: #FF9CAA;
    padding: 10px 12px;
}
/* The one line the system speaks on, in the middle of the chart header. */
QLabel#systemNotice {
    color: #F2C86B;
    padding: 4px 10px;
}
QMessageBox {
    background: #141A22;
    color: #F1F5F9;
}
QMessageBox QLabel {
    background: transparent;
    color: #F1F5F9;
}
/* These boxes carry paragraphs, not a line, and every irreversible answer in
   the app is asked for in one of them. Padding on the box itself is ignored,
   so the room is taken here: off the icon on the left, off the frame on the
   right, and under each block so it does not run into the next. */
QMessageBox QLabel#qt_msgbox_label {
    padding: 2px 4px 10px 8px;
}
QMessageBox QLabel#qt_msgbox_informativelabel {
    padding: 0 4px 14px 8px;
}
QMessageBox QDialogButtonBox {
    padding-left: 8px;
}
/* Room around the words rather than a width for them: min-width here makes
   Qt size every button from that number and forget the text, which clipped
   the longest answer in the box down to "Close at Marke". */
QMessageBox QPushButton {
    padding: 0 20px;
}
"""


def apply_dark_appearance() -> None:
    """Tell the platform the app is dark, so it draws its own parts dark.

    A stylesheet reaches the widgets Qt paints itself and stops at the ones
    the operating system paints — scroll bars, focus rings, the text cursor
    and selection. On a Mac set to Light those are drawn for a light window
    and all but disappear against this theme. Saying which scheme the app is
    in is what keeps them native and visible at once.
    """
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
