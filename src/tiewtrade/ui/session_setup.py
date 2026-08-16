from collections.abc import Iterable
from decimal import Decimal

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
    preset_choices,
)
from tiewtrade.application.paper_symbols import PAPER_SYMBOLS
from tiewtrade.market_data.config import SUPPORTED_V1_TIMEFRAME_CHOICES
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.pair_text import pair_text


class SessionSetupWidget(QWidget):
    create_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionSetup")
        self._field_errors: dict[str, QLabel] = {}

        self.trade_mode_label = QLabel("Paper")
        self.trade_mode_label.setObjectName("readOnlyValue")

        self.market_type = QComboBox()
        self.market_type.setObjectName("marketType")
        self.market_type.addItem("Spot", "spot")
        self.market_type.addItem("Futures", "futures")

        self.symbol = QComboBox()
        self.symbol.setObjectName("symbol")
        for symbol in PAPER_SYMBOLS:
            self.symbol.addItem(pair_text(symbol), symbol)

        self.timeframe = QComboBox()
        self.timeframe.setObjectName("timeframe")
        for timeframe in SUPPORTED_V1_TIMEFRAME_CHOICES:
            self.timeframe.addItem(timeframe, timeframe)
        self.timeframe.setCurrentIndex(self.timeframe.findData("5m"))

        self.available_capital = QLineEdit()
        self.available_capital.setObjectName("availableCapital")
        self.available_capital.setPlaceholderText("Enter available USDT")
        self.available_capital_error = self._error_label("available_capital")

        self.max_entries = QSpinBox()
        self.max_entries.setObjectName("maxEntries")
        self.max_entries.setRange(2, 20)
        self.max_entries.setSingleStep(2)
        self.max_entries.setValue(10)
        self.max_entries_error = self._error_label("max_entries")

        self.strategy = QComboBox()
        self.strategy.setObjectName("strategy")

        self.spot_ratio = QLineEdit("80")
        self.spot_ratio.setObjectName("spotTradingCapitalPercent")
        self.spot_ratio.setPlaceholderText("Percent")
        self.spot_ratio_error = self._error_label("spot_trading_capital_percent")

        futures_policy = FuturesTradingPolicy.v1(
            leverage=FuturesTradingPolicy.V1_MINIMUM_LEVERAGE
        )
        self.leverage = QSpinBox()
        self.leverage.setObjectName("futuresLeverage")
        self.leverage.setRange(
            FuturesTradingPolicy.V1_MINIMUM_LEVERAGE,
            FuturesTradingPolicy.V1_MAXIMUM_LEVERAGE,
        )
        self.leverage.setValue(futures_policy.leverage)

        self.symbol_error = self._error_label("symbol")
        self.timeframe_error = self._error_label("timeframe")
        self.market_type_error = self._error_label("market_type")
        self.strategy_error = self._error_label("preset_version")

        self.create_button = QPushButton("Start Paper Bot")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self._submit)

        self.market_type.setAccessibleName("Market Type")
        self.symbol.setAccessibleName("Pair")
        self.timeframe.setAccessibleName("Timeframe")
        self.strategy.setAccessibleName("Strategy")
        self.available_capital.setAccessibleName("Available Capital (USDT)")
        self.max_entries.setAccessibleName("Maximum Entries")
        self.spot_ratio.setAccessibleName("Spot Trading Capital (%)")
        self.leverage.setAccessibleName("Futures Leverage")
        self.create_button.setAccessibleName("Start Paper Bot")

        self._build_layout()
        self.market_type.currentIndexChanged.connect(self._sync_market_fields)
        self._sync_market_fields()

    def values(self) -> PaperSessionSetupValues:
        futures = self.market_type.currentData() == "futures"
        return PaperSessionSetupValues(
            market_type=str(self.market_type.currentData()),
            symbol=str(self.symbol.currentData()),
            timeframe=str(self.timeframe.currentData()),
            available_capital=self.available_capital.text().strip(),
            max_entries=str(self.max_entries.value()),
            spot_trading_capital_percent=(
                None if futures else self.spot_ratio.text().strip()
            ),
            futures_leverage=str(self.leverage.value()) if futures else None,
            preset_version=str(self.strategy.currentData()),
        )

    def set_values_from_session(self, session: ConfiguredPaperSession) -> None:
        config = session.config
        self.market_type.setCurrentIndex(
            self.market_type.findData(config.market_type.value)
        )
        self.symbol.setCurrentIndex(self.symbol.findData(session.market_data.symbol))
        self.timeframe.setCurrentIndex(
            self.timeframe.findData(session.market_data.timeframe)
        )
        self.available_capital.setText(format(config.available_capital, "f"))
        self.max_entries.setValue(config.entry_policy.max_entries)
        if config.spot_policy is not None:
            self.spot_ratio.setText(
                format(config.spot_policy.trading_capital_ratio * Decimal("100"), "f")
            )
        if config.futures_policy is not None:
            self.leverage.setValue(config.futures_policy.leverage)
        self._populate_strategy()
        strategy_index = self.strategy.findData(config.preset_version)
        if strategy_index >= 0:
            self.strategy.setCurrentIndex(strategy_index)
        self.clear_errors()

    @Slot()
    def _submit(self) -> None:
        if self.create_button.isEnabled():
            self.create_requested.emit(self.values())

    def set_loading(self, loading: bool) -> None:
        for control in (
            self.market_type,
            self.symbol,
            self.timeframe,
            self.strategy,
            self.available_capital,
            self.max_entries,
            self.spot_ratio,
            self.leverage,
            self.create_button,
        ):
            control.setDisabled(loading)
        self.create_button.setText("Starting…" if loading else "Start Paper Bot")

    def clear_errors(self) -> None:
        for error_label in self._field_errors.values():
            error_label.clear()
            error_label.setVisible(False)

    def show_field_error(self, field: str, message: str) -> None:
        error_label = self._field_errors.get(field)
        if error_label is None:
            return
        error_label.setText(message)
        error_label.setVisible(True)

    def reveal_validation_field(self, field: str) -> tuple[QWidget, QLabel] | None:
        if field == "available_capital":
            return self.available_capital, self.available_capital_error
        if field == "market_type":
            return self.market_type, self.market_type_error
        if field == "max_entries":
            return self.max_entries, self.max_entries_error
        if field == "spot_trading_capital_percent":
            return self.spot_ratio, self.spot_ratio_error
        if field == "symbol":
            return self.symbol, self.symbol_error
        if field == "timeframe":
            return self.timeframe, self.timeframe_error
        if field == "preset_version":
            return self.strategy, self.strategy_error
        return None

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(20)

        # One list of settings, top to bottom. A single form is also what
        # keeps every field on one left edge without being asked to.
        self._form_layout = self._form()
        self._add_row(
            self._form_layout,
            "Market Type",
            self.market_type,
            (self.market_type_error,),
        )
        self._add_row(
            self._form_layout,
            "Pair",
            self.symbol,
            (self.symbol_error,),
        )
        self._add_row(
            self._form_layout,
            "Timeframe",
            self.timeframe,
            (self.timeframe_error,),
        )
        self._add_row(
            self._form_layout,
            "Strategy",
            self.strategy,
            (self.strategy_error,),
        )
        self._add_row(
            self._form_layout,
            "Available (USDT)",
            self.available_capital,
            (self.available_capital_error,),
        )
        self._add_row(
            self._form_layout,
            "Max Entries",
            self.max_entries,
            (self.max_entries_error,),
        )
        # The two markets ask for one number each, in the same place, so they
        # are two rows of this form that take turns rather than two panels.
        self.spot_row = self._add_row(
            self._form_layout,
            "Trading Ratio (%)",
            self.spot_ratio,
            (self.spot_ratio_error,),
        )
        self.futures_row = self._add_row(self._form_layout, "Leverage", self.leverage)
        card_layout.addLayout(self._form_layout)

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.create_button)
        card_layout.addLayout(actions)
        root.addWidget(card)
        root.addStretch()

    @Slot()
    def _sync_market_fields(self) -> None:
        futures = self.market_type.currentData() == "futures"
        self.spot_row.setVisible(not futures)
        self.futures_row.setVisible(futures)
        self._populate_strategy()

    def _populate_strategy(self) -> None:
        market_type = MarketType(self.market_type.currentData())
        previous = self.strategy.currentData()
        self.strategy.blockSignals(True)
        self.strategy.clear()
        for choice in preset_choices(market_type):
            self.strategy.addItem(choice.display_name, choice.preset_version)
        if previous is not None:
            index = self.strategy.findData(previous)
            if index >= 0:
                self.strategy.setCurrentIndex(index)
        self.strategy.blockSignals(False)

    @staticmethod
    def _form() -> QVBoxLayout:
        """Rows of a label, its field, and whatever the field has to say.

        Rows are built by hand rather than left to QFormLayout because the
        gap under a label has to be smaller than the gap between rows —
        one spacing for both is what makes a stacked form read as a wall.
        """
        form = QVBoxLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(16)
        return form

    def _add_row(
        self,
        form: QVBoxLayout,
        label: str,
        field: QWidget,
        supporting: Iterable[QWidget] = (),
    ) -> QWidget:
        """Add one row, and hand back the handle that can hide it again."""
        row = QWidget()
        stack = QVBoxLayout(row)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(4)
        stack.addWidget(QLabel(label))
        stack.addWidget(field)
        for widget in supporting:
            stack.addWidget(widget)
        form.addWidget(row)
        return row

    def _error_label(self, field: str) -> QLabel:
        label = QLabel()
        label.setObjectName("fieldError")
        label.setVisible(False)
        label.setWordWrap(True)
        self._field_errors[field] = label
        return label
