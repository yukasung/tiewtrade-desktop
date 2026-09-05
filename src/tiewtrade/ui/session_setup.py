from collections.abc import Iterable

from PySide6.QtCore import QLocale, Signal, Slot
from PySide6.QtGui import QDoubleValidator
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
    setup_values_of,
)
from tiewtrade.application.paper_symbols import PAPER_SYMBOLS
from tiewtrade.market_data.config import SUPPORTED_V1_TIMEFRAME_CHOICES
from tiewtrade.trading.cooldown_policy import CooldownMode, CooldownPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.money_text import QUOTE_ASSET
from tiewtrade.ui.pair_text import pair_text

_AMOUNT_LABEL = f"Amount ({QUOTE_ASSET})"
_AMOUNT_DECIMALS = 8
"""As many decimals as the quote asset is ever stated in."""

_DEFAULT_COOLDOWN_DAYS = 7
"""What the days field opens on, for a trader who chooses to wait one out."""


class SessionSetupWidget(QWidget):
    create_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionSetup")
        # A field is only reachable by name once both halves are registered
        # under it, so the control a validation error scrolls to and the label
        # that error is written into cannot drift apart.
        self._field_errors: dict[str, QLabel] = {}
        self._field_controls: dict[str, QWidget] = {}

        self.trade_mode_label = QLabel("Paper")
        self.trade_mode_label.setObjectName("readOnlyValue")

        self.market_type = QComboBox()
        self.market_type.setObjectName("marketType")
        self.market_type.addItem("Spot", MarketType.SPOT.value)
        self.market_type.addItem("Futures", MarketType.FUTURES.value)

        self.symbol = QComboBox()
        self.symbol.setObjectName("symbol")
        for symbol in PAPER_SYMBOLS:
            self.symbol.addItem(pair_text(symbol), symbol)

        self.timeframe = QComboBox()
        self.timeframe.setObjectName("timeframe")
        for timeframe in SUPPORTED_V1_TIMEFRAME_CHOICES:
            self.timeframe.addItem(timeframe, timeframe)
        self.timeframe.setCurrentIndex(self.timeframe.findData("5m"))

        self.capital_per_entry = QLineEdit()
        self.capital_per_entry.setObjectName("capitalPerEntry")
        self.capital_per_entry.setPlaceholderText(f"Enter {QUOTE_ASSET} amount")
        # An Amount is a number, so the field turns away what is not one as it
        # is typed rather than letting the form be submitted to find out. It
        # still lets an empty field through — a blank Amount is a missing
        # Amount, which the application is the one to say. The C locale is
        # what keeps the decimal point a point, whatever the machine is set to.
        amount_validator = QDoubleValidator(self.capital_per_entry)
        amount_validator.setBottom(0)
        amount_validator.setDecimals(_AMOUNT_DECIMALS)
        amount_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        amount_validator.setLocale(QLocale.c())
        self.capital_per_entry.setValidator(amount_validator)
        self.capital_per_entry_error = self._error_label(
            "capital_per_entry", self.capital_per_entry
        )

        self.max_entries = QSpinBox()
        self.max_entries.setObjectName("maxEntries")
        self.max_entries.setRange(2, 20)
        self.max_entries.setSingleStep(2)
        self.max_entries.setValue(10)
        self.max_entries_error = self._error_label("max_entries", self.max_entries)

        self.cooldown_mode = QComboBox()
        self.cooldown_mode.setObjectName("cooldownMode")
        self.cooldown_mode.addItem(
            "Until cancelled", CooldownMode.UNTIL_CANCELLED.value
        )
        self.cooldown_mode.addItem(
            "After a set number of days", CooldownMode.DURATION.value
        )
        self.cooldown_mode.addItem("Off", CooldownMode.OFF.value)
        self.cooldown_mode_error = self._error_label(
            "cooldown_mode", self.cooldown_mode
        )

        self.cooldown_days = QSpinBox()
        self.cooldown_days.setObjectName("cooldownDays")
        self.cooldown_days.setRange(
            CooldownPolicy.MINIMUM_DAYS, CooldownPolicy.MAXIMUM_DAYS
        )
        self.cooldown_days.setValue(_DEFAULT_COOLDOWN_DAYS)
        self.cooldown_days_error = self._error_label(
            "cooldown_days", self.cooldown_days
        )

        self.strategy = QComboBox()
        self.strategy.setObjectName("strategy")

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

        self.symbol_error = self._error_label("symbol", self.symbol)
        self.timeframe_error = self._error_label("timeframe", self.timeframe)
        self.market_type_error = self._error_label("market_type", self.market_type)
        self.strategy_error = self._error_label("preset_version", self.strategy)

        self.create_button = QPushButton("Start Paper Bot")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self._submit)

        self.market_type.setAccessibleName("Market Type")
        self.symbol.setAccessibleName("Pair")
        self.timeframe.setAccessibleName("Timeframe")
        self.strategy.setAccessibleName("Strategy")
        self.capital_per_entry.setAccessibleName(_AMOUNT_LABEL)
        self.max_entries.setAccessibleName("Maximum Entries")
        self.cooldown_mode.setAccessibleName("Cooldown")
        self.cooldown_days.setAccessibleName("Cooldown Days")
        self.leverage.setAccessibleName("Futures Leverage")
        self.create_button.setAccessibleName("Start Paper Bot")

        self._build_layout()
        self.market_type.currentIndexChanged.connect(self._sync_market_fields)
        self.cooldown_mode.currentIndexChanged.connect(self._sync_cooldown_fields)
        self._sync_market_fields()
        self._sync_cooldown_fields()

    def values(self) -> PaperSessionSetupValues:
        futures = self._market_type() is MarketType.FUTURES
        waits = self._cooldown_mode() is CooldownMode.DURATION
        return PaperSessionSetupValues(
            market_type=str(self.market_type.currentData()),
            symbol=str(self.symbol.currentData()),
            timeframe=str(self.timeframe.currentData()),
            capital_per_entry=self.capital_per_entry.text().strip(),
            max_entries=str(self.max_entries.value()),
            cooldown_mode=str(self.cooldown_mode.currentData()),
            cooldown_days=str(self.cooldown_days.value()) if waits else None,
            futures_leverage=str(self.leverage.value()) if futures else None,
            preset_version=str(self.strategy.currentData()),
        )

    def set_values_from_session(self, session: ConfiguredPaperSession) -> None:
        """Show a Session on the form, in the strings it is read back in.

        Filled from `setup_values_of` rather than from the config directly, so
        an untouched form compares equal to the Session it came from.
        """
        values = setup_values_of(session)
        self.market_type.setCurrentIndex(self.market_type.findData(values.market_type))
        self.symbol.setCurrentIndex(self.symbol.findData(values.symbol))
        self.timeframe.setCurrentIndex(self.timeframe.findData(values.timeframe))
        self.capital_per_entry.setText(values.capital_per_entry)
        self.max_entries.setValue(int(values.max_entries))
        self.cooldown_mode.setCurrentIndex(
            self.cooldown_mode.findData(values.cooldown_mode)
        )
        if values.cooldown_days is not None:
            self.cooldown_days.setValue(int(values.cooldown_days))
        self._sync_cooldown_fields()
        if values.futures_leverage is not None:
            self.leverage.setValue(int(values.futures_leverage))
        self._populate_strategy()
        strategy_index = self.strategy.findData(values.preset_version)
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
            self.capital_per_entry,
            self.max_entries,
            self.cooldown_mode,
            self.cooldown_days,
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
        """The control at fault and the line its error is written on.

        A field the form does not carry reads as None rather than raising: a
        validation error names a field the application knows about, and one
        this form has no row for is nothing it can scroll into view.
        """
        control = self._field_controls.get(field)
        error_label = self._field_errors.get(field)
        if control is None or error_label is None:
            return None
        return control, error_label

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
            _AMOUNT_LABEL,
            self.capital_per_entry,
            (self.capital_per_entry_error,),
        )
        self._add_row(
            self._form_layout,
            "Max Entries",
            self.max_entries,
            (self.max_entries_error,),
        )
        self._add_row(
            self._form_layout,
            "Cooldown",
            self.cooldown_mode,
            (self.cooldown_mode_error,),
        )
        # A Cooldown asks for a number only where it waits out one, so this
        # row comes and goes the way the Leverage row below does.
        self.cooldown_days_row = self._add_row(
            self._form_layout,
            "Cooldown Days",
            self.cooldown_days,
            (self.cooldown_days_error,),
        )
        # Futures asks for one number more than Spot does, so it is a row of
        # this form that appears and goes rather than a panel of its own.
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
        futures = self._market_type() is MarketType.FUTURES
        self.futures_row.setVisible(futures)
        self._populate_strategy()

    @Slot()
    def _sync_cooldown_fields(self) -> None:
        self.cooldown_days_row.setVisible(
            self._cooldown_mode() is CooldownMode.DURATION
        )

    def _cooldown_mode(self) -> CooldownMode:
        return CooldownMode(self.cooldown_mode.currentData())

    def _market_type(self) -> MarketType:
        return MarketType(self.market_type.currentData())

    def _populate_strategy(self) -> None:
        market_type = self._market_type()
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

    def _error_label(self, field: str, control: QWidget) -> QLabel:
        """The line a field's error is written on, registered with the field.

        The control is taken here rather than in a second call so that a field
        cannot be given somewhere to say what is wrong without also being
        somewhere the form can scroll to.
        """
        label = QLabel()
        label.setObjectName("fieldError")
        label.setVisible(False)
        label.setWordWrap(True)
        self._field_errors[field] = label
        self._field_controls[field] = control
        return label
