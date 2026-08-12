from decimal import Decimal

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.spot_policy import SpotTradingPolicy
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.preset_display import preset_display_name

_BLANK = "—"
_UNAVAILABLE = "Unavailable"


class SessionOverviewWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionOverview")

        self.market_value = self._value_label()
        self.symbol_value = self._value_label()
        self.timeframe_value = self._value_label()
        self.preset_value = self._value_label()
        self.available_capital_value = self._value_label()
        self.max_entries_value = self._value_label()
        self.spot_ratio_value = self._value_label()
        self.spot_reserve_ratio_value = self._value_label()
        self.margin_mode_value = self._value_label()
        self.position_mode_value = self._value_label()
        self.fee_value = self._value_label()
        self.slippage_value = self._value_label()
        self.created_at_value = self._value_label()

        self._build_layout()

    def show_session(self, session: ConfiguredPaperSession) -> None:
        config = session.config
        self.market_value.setText(config.market_type.value.title())
        self.symbol_value.setText(session.market_data.symbol)
        self.timeframe_value.setText(session.market_data.timeframe)
        self.preset_value.setText(preset_display_name(config.preset_version))
        self.available_capital_value.setText(
            f"{_decimal_text(config.available_capital)} USDT"
        )
        self.max_entries_value.setText(str(config.entry_policy.max_entries))
        self._show_market_policy(session)
        self.fee_value.setText(_percent_text(config.fee_rate))
        self.slippage_value.setText(f"{_decimal_text(config.slippage_bps)} bps")
        self.created_at_value.setText(display_timestamp(session.created_at_utc))

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        details = QFrame()
        details.setObjectName("card")
        grid = QGridLayout(details)
        grid.setContentsMargins(24, 24, 24, 24)
        grid.setHorizontalSpacing(48)
        grid.setVerticalSpacing(18)
        rows = (
            ("Market Type", self.market_value),
            ("Symbol", self.symbol_value),
            ("Timeframe", self.timeframe_value),
            ("Strategy Preset", self.preset_value),
            ("Available Capital", self.available_capital_value),
            ("Max Entries", self.max_entries_value),
            ("Spot Trading Capital Ratio", self.spot_ratio_value),
            ("Spot Reserve Ratio", self.spot_reserve_ratio_value),
            ("Margin Mode", self.margin_mode_value),
            ("Position Mode", self.position_mode_value),
            ("Trading Fee", self.fee_value),
            ("Slippage", self.slippage_value),
            ("Created at", self.created_at_value),
        )
        for index, (label, value) in enumerate(rows):
            column = index % 2
            row = (index // 2) * 2
            # An odd count leaves the last pair alone on its line; let it use
            # the whole width rather than clipping inside half of it.
            span = 2 if column == 0 and index == len(rows) - 1 else 1
            label_widget = QLabel(label)
            label_widget.setObjectName("detailLabel")
            grid.addWidget(label_widget, row, column, 1, span)
            grid.addWidget(value, row + 1, column, 1, span)
        root.addWidget(details)
        root.addStretch()

    def _show_market_policy(self, session: ConfiguredPaperSession) -> None:
        """Fill the pair belonging to this market and blank the other one.

        A Session carries the policy for its own market and no other, so one
        holding both is malformed: its pair reads Unavailable rather than
        showing a policy the Session never chose.
        """
        config = session.config
        spot = config.spot_policy
        futures = config.futures_policy
        if config.market_type is MarketType.SPOT:
            self._show_spot_policy(spot if futures is None else None)
            self._show_futures_policy(None, missing=_BLANK)
            return
        self._show_spot_policy(None, missing=_BLANK)
        self._show_futures_policy(futures if spot is None else None)

    def _show_spot_policy(
        self,
        policy: SpotTradingPolicy | None,
        *,
        missing: str = _UNAVAILABLE,
    ) -> None:
        if policy is None:
            self.spot_ratio_value.setText(missing)
            self.spot_reserve_ratio_value.setText(missing)
            return
        self.spot_ratio_value.setText(_percent_text(policy.trading_capital_ratio))
        self.spot_reserve_ratio_value.setText(_percent_text(policy.reserve_ratio))

    def _show_futures_policy(
        self,
        policy: FuturesTradingPolicy | None,
        *,
        missing: str = _UNAVAILABLE,
    ) -> None:
        if policy is None:
            self.margin_mode_value.setText(missing)
            self.position_mode_value.setText(missing)
            return
        self.margin_mode_value.setText(f"{policy.margin_mode.value.title()} Margin")
        self.position_mode_value.setText(
            f"{policy.position_mode.value.replace('_', '-').capitalize()} Mode"
        )

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel("—")
        label.setObjectName("detailValue")
        label.setTextInteractionFlags(label.textInteractionFlags())
        return label


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _percent_text(ratio: Decimal) -> str:
    return f"{_decimal_text(ratio * Decimal('100'))}%"
