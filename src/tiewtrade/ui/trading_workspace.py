from datetime import UTC, datetime
from enum import IntEnum

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.bot_control import BotControlSnapshot, configured_bot_control
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trading_workspace import TradingWorkspaceSnapshot
from tiewtrade.ui.bot_control import BotControlWidget
from tiewtrade.ui.candlestick_chart import CandlestickChartWidget
from tiewtrade.ui.connection_notice import ConnectionNoticeCard
from tiewtrade.ui.position_basket_table import PositionBasketTable
from tiewtrade.ui.session_setup import SessionSetupWidget
from tiewtrade.ui.trade_history_page import TradeHistoryPage
from tiewtrade.ui.trade_history_workflow import HistoryTab

BOT_CONTROL_WIDTH = 360


class WorkspaceTab(IntEnum):
    """The tabs under the chart, in the order they are read left to right."""

    POSITION_BASKET = 0
    BASKET_HISTORY = 1
    TRADE_HISTORY = 2


# The screen says Position where the domain says Basket: a Basket is one
# round of trading, which is what a trader already calls a position.
_TAB_TITLES = ("Position", "Position History", "Trade History")


class TradingWorkspace(QWidget):
    trade_history_activated = Signal()
    start_bot_requested = Signal()
    stop_bot_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspace")

        self.setup = SessionSetupWidget()
        self.bot_control_widget = BotControlWidget()
        self.overview = self.bot_control_widget.overview
        self.position_basket = PositionBasketTable()
        self.trade_history = TradeHistoryPage()

        self.unavailable_panel = QFrame()
        self.unavailable_message = QLabel()
        self.unavailable_message.setObjectName("unavailableMessage")
        self.unavailable_message.setWordWrap(True)
        self.unavailable_retry_button = QPushButton("Try Again")
        self.unavailable_retry_button.setProperty("retryButton", True)

        self.chart = CandlestickChartWidget()
        self.connection_notice_card = ConnectionNoticeCard()

        self.workspace_header = QFrame()
        self.workspace_header.setObjectName("workspaceToolbar")
        self.toolbar_title = QLabel("TiewTrade")
        self.toolbar_title.setObjectName("toolbarTitle")
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.clicked.connect(self._show_settings_placeholder)
        header_layout = QHBoxLayout(self.workspace_header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.addWidget(self.toolbar_title)
        header_layout.addStretch()
        header_layout.addWidget(self.settings_button)

        self._bot_pages = QStackedWidget()
        self._bot_pages.setObjectName("botControlPages")
        self._bot_pages.addWidget(self.setup)
        self._bot_pages.addWidget(self.bot_control_widget)
        self._bot_pages.addWidget(self.unavailable_panel)
        self.bot_control_widget.start_requested.connect(self.start_bot_requested)
        self.bot_control_widget.stop_requested.connect(self.stop_bot_requested)

        # One strip of tabs, not two. Basket History and Trade History are two
        # readings of the same records rather than a place of their own, so
        # they stand beside the open Position instead of behind a second row.
        self.tabs = QTabBar()
        self.tabs.setObjectName("workspaceTabs")
        self.tabs.setExpanding(False)
        # Qt rules a line under the strip to close off a pane below it. There
        # is no pane here, only the table these tabs name.
        self.tabs.setDrawBase(False)
        self.tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for title in _TAB_TITLES:
            self.tabs.addTab(title)
        self.tab_pages = QStackedWidget()
        self.tab_pages.setObjectName("workspaceTabPages")
        self.tab_pages.addWidget(self.position_basket)
        self.tab_pages.addWidget(self.trade_history)
        self.tabs.currentChanged.connect(self._tab_changed)

        unavailable_layout = QVBoxLayout(self.unavailable_panel)
        unavailable_layout.addWidget(self.unavailable_message)
        unavailable_layout.addWidget(self.unavailable_retry_button)
        unavailable_layout.addStretch()

        self._build_layout()

    @Slot()
    def show_setup(self) -> None:
        self._activate_setup()

    @Slot(object)
    def show_workspace_snapshot(self, value: object) -> None:
        if not isinstance(value, TradingWorkspaceSnapshot):
            return
        self.position_basket.show_snapshot(value.position_basket)

    @Slot(str)
    def show_setup_for_validation(self, field: str) -> None:
        self._activate_setup()
        validation_widgets = self.setup.reveal_validation_field(field)
        if validation_widgets is None:
            return
        control, error_label = validation_widgets
        self.setup.adjustSize()
        self._bot_pages.adjustSize()
        control.setFocus(Qt.FocusReason.OtherFocusReason)
        self.bot_control_scroll.ensureWidgetVisible(error_label)

    def _activate_setup(self) -> None:
        self._show_bot_page(self.setup)

    def show_configured_session(self, session: ConfiguredPaperSession) -> None:
        self.show_bot_control_snapshot(
            configured_bot_control(
                session,
                observed_at_utc=datetime.now(UTC),
            )
        )

    @Slot(object)
    def show_bot_control_snapshot(self, value: object) -> None:
        if not isinstance(value, BotControlSnapshot):
            return
        self.bot_control_widget.show_snapshot(value)
        self.show_workspace_snapshot(value.workspace)
        self._show_bot_page(self.bot_control_widget)

    def show_unavailable(self, message: str) -> None:
        self.unavailable_message.setText(message)
        self._show_bot_page(self.unavailable_panel)

    def set_bot_busy(self, busy: bool) -> None:
        self.setup.set_loading(busy)
        self.unavailable_retry_button.setDisabled(busy)

    @Slot()
    def _show_settings_placeholder(self) -> None:
        QMessageBox.information(self, "Settings", "Coming soon.")

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self._body = QWidget()
        self._body.setObjectName("workspaceContent")
        self._body_layout = QHBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(12)

        primary = QWidget()
        primary_layout = QVBoxLayout(primary)
        primary_layout.setContentsMargins(0, 0, 0, 0)
        primary_layout.setSpacing(12)
        primary_layout.addWidget(self.chart, 3)
        # The tabs and the table they switch between are one surface, so the
        # padding belongs out here rather than as a border between them.
        tabbed = QWidget()
        tabbed.setObjectName("workspaceTabbedPane")
        tabbed_layout = QVBoxLayout(tabbed)
        tabbed_layout.setContentsMargins(16, 8, 16, 0)
        tabbed_layout.setSpacing(8)
        # The Cancel Cooldown action reads at the far right of this row, at
        # the same height as the tabs beside it — not inside the Position
        # tab's own table, which redraws under it on every price. A stretch
        # on both sides of the tabs keeps them centred the way they read
        # before the action joined their row, instead of pinned to the left
        # by the stretch the action alone would need on its own side.
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.addStretch(1)
        tabs_row.addWidget(self.tabs)
        tabs_row.addStretch(1)
        tabs_row.addWidget(self.position_basket.cancel_cooldown_button)
        tabs_row.addWidget(self.position_basket.close_all_button)
        tabbed_layout.addLayout(tabs_row)
        tabbed_layout.addWidget(self.tab_pages, 1)
        primary_layout.addWidget(tabbed, 2)

        self.bot_control = QFrame()
        self.bot_control.setObjectName("botControl")
        self.bot_control.setFixedWidth(BOT_CONTROL_WIDTH)
        bot_layout = QVBoxLayout(self.bot_control)
        self.bot_control_scroll = QScrollArea()
        self.bot_control_scroll.setObjectName("botControlScroll")
        self.bot_control_scroll.viewport().setObjectName("botControlViewport")
        self.bot_control_scroll.setWidgetResizable(True)
        self.bot_control_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.bot_control_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._bot_pages.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Minimum,
        )
        self.bot_control_scroll.setWidget(self._bot_pages)
        bot_layout.addWidget(self.bot_control_scroll, 1)

        self.workspace_header.setFixedWidth(BOT_CONTROL_WIDTH)
        side_column = QWidget()
        side_layout = QVBoxLayout(side_column)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)
        side_layout.addWidget(self.workspace_header)
        side_layout.addWidget(self.connection_notice_card)
        side_layout.addWidget(self.bot_control, 1)

        self._body_layout.addWidget(primary, 1)
        self._body_layout.addWidget(side_column)
        root.addWidget(self._body, 1)

    def show_tab(self, tab: WorkspaceTab) -> None:
        """Show one of the three readings the tab strip offers."""
        # Setting the index announces the change itself; it stays silent only
        # when the index already holds, which is the one case to answer here.
        if self.tabs.currentIndex() == tab.value:
            self._tab_changed(tab.value)
            return
        self.tabs.setCurrentIndex(tab.value)

    @property
    def current_tab(self) -> WorkspaceTab:
        return WorkspaceTab(self.tabs.currentIndex())

    @Slot(int)
    def _tab_changed(self, index: int) -> None:
        tab = WorkspaceTab(index)
        if tab is WorkspaceTab.POSITION_BASKET:
            self.tab_pages.setCurrentWidget(self.position_basket)
            return
        self.tab_pages.setCurrentWidget(self.trade_history)
        self.trade_history.show_history_tab(
            HistoryTab.BASKETS
            if tab is WorkspaceTab.BASKET_HISTORY
            else HistoryTab.TRADES
        )
        self.trade_history_activated.emit()

    def _show_bot_page(self, page: QWidget) -> None:
        self._bot_pages.setCurrentWidget(page)
        self.bot_control_scroll.horizontalScrollBar().setValue(0)
        self.bot_control_scroll.verticalScrollBar().setValue(0)
