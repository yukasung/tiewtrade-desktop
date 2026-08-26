from decimal import Decimal

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    preset_display_name,
)
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.money_text import QUOTE_ASSET
from tiewtrade.ui.pair_text import pair_text

_UNAVAILABLE = "Unavailable"


class SessionOverviewWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionOverview")

        self.market_value = self._value_label()
        self.symbol_value = self._value_label()
        self.timeframe_value = self._value_label()
        self.preset_value = self._value_label()
        self.capital_per_entry_value = self._value_label()
        self.max_entries_value = self._value_label()
        self.leverage_value = self._value_label()
        self.margin_mode_value = self._value_label()
        self.position_mode_value = self._value_label()
        self.created_at_value = self._value_label()

        self._build_layout()

    def show_session(self, session: ConfiguredPaperSession) -> None:
        config = session.config
        self.market_value.setText(config.market_type.value.title())
        self.symbol_value.setText(pair_text(session.market_data.symbol))
        self.timeframe_value.setText(session.market_data.timeframe)
        self.preset_value.setText(preset_display_name(config.preset_version))
        self.capital_per_entry_value.setText(
            f"{_amount_text(config.capital_per_entry)} {QUOTE_ASSET}"
        )
        self.max_entries_value.setText(str(config.entry_policy.max_entries))
        self._show_market_policy(session)
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
            ("Pair", self.symbol_value),
            ("Timeframe", self.timeframe_value),
            ("Strategy Preset", self.preset_value),
            ("Amount", self.capital_per_entry_value),
            ("Max Entries", self.max_entries_value),
            ("Leverage", self.leverage_value),
            ("Margin Mode", self.margin_mode_value),
            ("Position Mode", self.position_mode_value),
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
        """Fill the Futures rows, and blank them for the market that has none.

        Only Futures carries a policy of its own. A Spot Session trades the
        capital it was given under nothing but its Entry count, so the rows
        read as blank — unless it holds a Futures policy, which is malformed:
        then they read Unavailable rather than showing settings a Spot Session
        never chose.
        """
        config = session.config
        if config.market_type is MarketType.SPOT:
            blank = config.futures_policy is None
            self._show_futures_policy(None, missing=BLANK if blank else _UNAVAILABLE)
            return
        self._show_futures_policy(config.futures_policy)

    def _show_futures_policy(
        self,
        policy: FuturesTradingPolicy | None,
        *,
        missing: str = _UNAVAILABLE,
    ) -> None:
        if policy is None:
            self.leverage_value.setText(missing)
            self.margin_mode_value.setText(missing)
            self.position_mode_value.setText(missing)
            return
        # Shown because it is the only Futures setting the trader picks, and
        # because the Amount above reads as margin: what an Entry opens is
        # that figure times this one, which the card cannot say without it.
        self.leverage_value.setText(f"{policy.leverage}x")
        self.margin_mode_value.setText(f"{policy.margin_mode.value.title()} Margin")
        self.position_mode_value.setText(
            f"{policy.position_mode.value.replace('_', '-').capitalize()} Mode"
        )

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel(BLANK)
        label.setObjectName("detailValue")
        label.setTextInteractionFlags(label.textInteractionFlags())
        return label


def _amount_text(value: Decimal) -> str:
    """A figure as it reads on screen, without the zeros it does not need.

    `normalize` is what makes 1000.00 read as 1000. It is a reading, not a
    value: the same figure is written to the database in full, because a
    trailing zero there is precision the record was given.
    """
    return format(value.normalize(), "f")
