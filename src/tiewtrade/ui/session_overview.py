from decimal import Decimal

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    preset_display_name,
)
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.cooldown_text import cooldown_text
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.money_text import QUOTE_ASSET
from tiewtrade.ui.pair_text import pair_text

_UNAVAILABLE = "Unavailable"


class SessionOverviewWidget(QWidget):
    """What a Session was pinned to, as rows a reader can run down.

    Only the rows that have something to say are laid out. A Spot Session has
    no leverage, no margin mode and no position mode — not "none yet", but
    none at all — and three rows reading blank take up half the card to repeat
    what the Market Type at the top already said.
    """

    # Every row the card can show, in reading order. What is actually laid
    # out is chosen per Session, so the two-column pairing below follows the
    # rows on screen rather than the rows that exist.
    _SHARED_LABELS = (
        "Market Type",
        "Pair",
        "Timeframe",
        "Strategy Preset",
        "Amount",
        "Max Entries",
        "Cooldown",
    )
    _FUTURES_LABELS = ("Leverage", "Margin Mode", "Position Mode")
    _CREATED_LABEL = "Created at"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionOverview")

        self.market_value = self._value_label()
        self.symbol_value = self._value_label()
        self.timeframe_value = self._value_label()
        self.preset_value = self._value_label()
        self.capital_per_entry_value = self._value_label()
        self.max_entries_value = self._value_label()
        self.cooldown_value = self._value_label()
        self.leverage_value = self._value_label()
        self.margin_mode_value = self._value_label()
        self.position_mode_value = self._value_label()
        self.created_at_value = self._value_label()

        self._values = {
            "Market Type": self.market_value,
            "Pair": self.symbol_value,
            "Timeframe": self.timeframe_value,
            "Strategy Preset": self.preset_value,
            "Amount": self.capital_per_entry_value,
            "Max Entries": self.max_entries_value,
            "Cooldown": self.cooldown_value,
            "Leverage": self.leverage_value,
            "Margin Mode": self.margin_mode_value,
            "Position Mode": self.position_mode_value,
            "Created at": self.created_at_value,
        }
        self._labels = {label: self._label(label) for label in self._values}
        self._laid_out: tuple[str, ...] = ()

        self._build_layout()
        self._lay_out_rows(
            (*self._SHARED_LABELS, *self._FUTURES_LABELS, self._CREATED_LABEL)
        )

    @property
    def visible_labels(self) -> tuple[str, ...]:
        """The rows the card is showing now, in the order they are read."""
        return self._laid_out

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
        self.cooldown_value.setText(cooldown_text(config.entry_policy.cooldown))
        self._show_market_policy(session)
        self.created_at_value.setText(display_timestamp(session.created_at_utc))

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        details = QFrame()
        details.setObjectName("card")
        self._grid = QGridLayout(details)
        self._grid.setContentsMargins(24, 24, 24, 24)
        self._grid.setHorizontalSpacing(48)
        self._grid.setVerticalSpacing(18)
        root.addWidget(details)
        root.addStretch()

    def _lay_out_rows(self, labels: tuple[str, ...]) -> None:
        """Put these rows in the card, two to a line, and take the rest out.

        Taken out rather than left blank, so the pairing closes up behind a
        row that is not shown instead of leaving a hole where it would have
        been. A widget out of the layout is hidden as well, or it would float
        at the corner of the card with nothing placing it.
        """
        if labels == self._laid_out:
            return
        for label in self._laid_out:
            for widget in (self._labels[label], self._values[label]):
                self._grid.removeWidget(widget)
                widget.hide()
        for index, label in enumerate(labels):
            column = index % 2
            row = (index // 2) * 2
            # An odd count leaves the last row alone on its line; let it use
            # the whole width rather than clipping inside half of it.
            span = 2 if column == 0 and index == len(labels) - 1 else 1
            for offset, widget in enumerate((self._labels[label], self._values[label])):
                self._grid.addWidget(widget, row + offset, column, 1, span)
                widget.show()
        self._laid_out = labels

    def _show_market_policy(self, session: ConfiguredPaperSession) -> None:
        """Fill the Futures rows, or take them out for the market with none.

        Only Futures carries a policy of its own. A Spot Session trades the
        capital it was given under nothing but its Entry count, so the rows
        are not laid out at all — unless it holds a Futures policy, which is
        malformed: then they are shown reading Unavailable, because a Session
        assembled wrongly is worth a row that says so rather than one the
        card quietly leaves out.
        """
        config = session.config
        futures_rows = (
            config.market_type is not MarketType.SPOT
            or config.futures_policy is not None
        )
        self._lay_out_rows(
            (
                *self._SHARED_LABELS,
                *(self._FUTURES_LABELS if futures_rows else ()),
                self._CREATED_LABEL,
            )
        )
        if config.market_type is MarketType.SPOT:
            # A row that is not laid out still holds text, and text saying a
            # setting is Unavailable is not what a Spot Session's rows mean.
            self._show_futures_policy(
                None, missing=BLANK if futures_rows is False else _UNAVAILABLE
            )
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
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("detailLabel")
        return label

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
