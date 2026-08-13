from datetime import UTC, datetime

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.bot_control import BotControlSnapshot, configured_bot_control
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trading_workspace import TradingWorkspaceSnapshot
from tiewtrade.ui.bot_control import BotControlWidget
from tiewtrade.ui.candlestick_chart import CandlestickChartWidget
from tiewtrade.ui.icons import gear_icon
from tiewtrade.ui.position_basket_table import PositionBasketTable
from tiewtrade.ui.session_setup import SessionSetupWidget
from tiewtrade.ui.theme import ICON_COLOR, ICON_HOVER_COLOR
from tiewtrade.ui.trade_history_page import TradeHistoryPage

BOT_CONTROL_WIDTH = 360
# The gear and the square it sits in, kept together because neither reads well
# without the other, and because the header they sit in has little height to
# spare: the chart below it will not go under 360px, so a taller header pushes
# the chart over the tables at the smallest window the app allows.
SETTINGS_ICON_SIZE = 22
SETTINGS_BUTTON_SIZE = 30
HEADER_MARGIN = 4


class TradingWorkspace(QWidget):
    trade_history_activated = Signal()
    start_bot_requested = Signal()
    stop_bot_requested = Signal()
    settings_requested = Signal()

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

        # The gear says all of itself; a word beside it would only repeat it.
        self.settings_button = QPushButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setIcon(
            gear_icon(SETTINGS_ICON_SIZE, ICON_COLOR, ICON_HOVER_COLOR)
        )
        self.settings_button.setIconSize(QSize(SETTINGS_ICON_SIZE, SETTINGS_ICON_SIZE))
        # setFixedSize would lose to the minimum every button gets from the
        # theme, so the square is asked for in the only language that outranks
        # it: a stylesheet on the button itself.
        self.settings_button.setStyleSheet(
            f"min-width: {SETTINGS_BUTTON_SIZE}px;"
            f" min-height: {SETTINGS_BUTTON_SIZE}px;"
        )
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self.settings_requested)

        self.chart = CandlestickChartWidget()

        self._bot_pages = QStackedWidget()
        self._bot_pages.setObjectName("botControlPages")
        self._bot_pages.addWidget(self.setup)
        self._bot_pages.addWidget(self.bot_control_widget)
        self._bot_pages.addWidget(self.unavailable_panel)
        self.bot_control_widget.start_requested.connect(self.start_bot_requested)
        self.bot_control_widget.stop_requested.connect(self.stop_bot_requested)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.position_basket, "Position / Basket")
        self.tabs.addTab(self.trade_history, "Trade History")
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

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.workspace_header = QFrame()
        self.workspace_header.setObjectName("workspaceHeader")
        header_layout = QHBoxLayout(self.workspace_header)
        header_layout.setContentsMargins(16, HEADER_MARGIN, 16, HEADER_MARGIN)
        brand = QLabel("TIEWTRADE")
        brand.setObjectName("brand")
        header_layout.addWidget(brand)
        header_layout.addStretch()
        header_layout.addWidget(self.settings_button)
        root.addWidget(self.workspace_header)

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
        primary_layout.addWidget(self.tabs, 2)

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

        self._body_layout.addWidget(primary, 1)
        self._body_layout.addWidget(self.bot_control)
        root.addWidget(self._body, 1)

    @Slot(int)
    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.trade_history:
            self.trade_history_activated.emit()

    def _show_bot_page(self, page: QWidget) -> None:
        self._bot_pages.setCurrentWidget(page)
        self.bot_control_scroll.horizontalScrollBar().setValue(0)
        self.bot_control_scroll.verticalScrollBar().setValue(0)
