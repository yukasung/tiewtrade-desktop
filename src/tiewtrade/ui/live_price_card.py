"""The price the market is at, kept on screen whatever the chart is showing.

The chart draws the current price only while it is following the candle
forming now: pan back a day and the line goes with the range it belonged to.
The price itself has not stopped arriving, so it is read here instead — a
card of its own, fed straight from the facts the feed publishes rather than
from the chart's view of them.

Beside it reads the other figure a trader looks at in the same glance: what
everything this installation has traded comes to, realised and unrealised
together. The two are read together because they are read against each
other — a price on its own says nothing about how the trading is going.
"""

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tiewtrade.application.chart_data import ChartCandle
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.money_text import cent_price_text, money_text
from tiewtrade.ui.sign_state import paint_by_sign, sign_colour
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


class LivePriceCard(QFrame):
    """Names one market, the last price seen in it, and what it all comes to."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("livePriceCard")
        self.pair = QLabel("")
        self.pair.setObjectName("eyebrow")
        self.price = QLabel(BLANK)
        self.price.setObjectName("livePrice")
        self.total_caption = QLabel("Total P/L")
        self.total_caption.setObjectName("eyebrow")
        self.total = QLabel(BLANK)
        self.total.setObjectName("totalPnl")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addLayout(_column(self.pair, self.price))
        layout.addStretch(1)
        layout.addLayout(
            _column(self.total_caption, self.total, align=Qt.AlignmentFlag.AlignRight)
        )
        self._stale = False
        self._market_type: MarketType | None = None
        # The colour the total is owed while it is live. It has no equivalent
        # of `show_price` to hand it back after a stale spell, so the card
        # remembers it instead.
        self._total_colour: str | None = None

    def show_pair(self, symbol: str, timeframe: str) -> None:
        self.pair.setText(" · ".join(self._pair_parts(symbol, timeframe)))

    def show_market_type(self, market_type: MarketType) -> None:
        """Name the market these prices are quoted in.

        A Session is pinned to one Market Type and only the pair is chosen
        after that, so this is set once and survives every later pair — it
        is not read off a price, which carries no market of its own.
        """
        self._market_type = market_type

    def _pair_parts(self, symbol: str, timeframe: str) -> tuple[str, ...]:
        parts = (symbol, timeframe)
        if self._market_type is None:
            return parts
        return (*parts, self._market_type.value.title())

    def show_price(self, candle: ChartCandle) -> None:
        """Read the newest price, coloured the way its candle is drawn.

        Green above its own open and red below it, which is the rule the
        chart's price line already uses — the card and the line are the same
        fact in two places and must not disagree about which way it went.
        """
        self.set_stale(False)
        self.show_pair(candle.symbol, candle.timeframe)
        self.price.setText(cent_price_text(candle.close))
        colour = POSITIVE_COLOR if candle.close >= candle.open else NEGATIVE_COLOR
        self.price.setStyleSheet(f"color: {colour};")

    def show_total_pnl(self, value: Decimal | None) -> None:
        """Everything realised plus what the open Basket is worth now.

        A blank is "no answer yet", which is not the same as break-even: zero
        across a whole ledger is a result, and it reads as one.
        """
        if value is None:
            self.total.setText(BLANK)
            self.total.setStyleSheet("")
            self._total_colour = None
            return
        self.total.setText(money_text(value))
        self._total_colour = sign_colour(value)
        if self._stale:
            # The dimming rule is in force, and an inline colour would beat
            # it. The figure is kept for `set_stale` to hand back.
            return
        paint_by_sign(self.total, value)

    def set_stale(self, stale: bool) -> None:
        """Say the price is no longer arriving, without taking it away.

        A blank card reads as a market that has nothing to say. What has
        happened is that the connection went, so the last price stays put
        and dims: still the last thing known, no longer the current one.

        The dimming is a theme rule, and a rule loses to the colour
        `show_price` sets on the label itself, so going stale hands that
        colour back.
        """
        if stale == self._stale:
            return
        self._stale = stale
        if stale:
            self.price.setStyleSheet("")
            self.total.setStyleSheet("")
        elif self._total_colour is not None:
            self.total.setStyleSheet(f"color: {self._total_colour};")
        self.setProperty("stale", stale)
        # The rule that dims reads the property off the card and names the
        # labels inside it, so the labels are the ones that have to be asked
        # for their colour again.
        style = self.style()
        for widget in (self, self.pair, self.price, self.total_caption, self.total):
            style.unpolish(widget)
            style.polish(widget)

    @property
    def is_stale(self) -> bool:
        return self._stale

    def clear(self) -> None:
        """Take the price back to a blank, and leave the total standing.

        Clearing follows a change of pair, and the total is not a fact about
        the pair being charted: it is what the whole database has realised
        plus the one Basket that is open, neither of which the chart moved.
        """
        self.set_stale(False)
        self.price.setText(BLANK)
        self.price.setStyleSheet("")


def _column(
    caption: QLabel,
    value: QLabel,
    *,
    align: Qt.AlignmentFlag | None = None,
) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    if align is not None:
        for label in (caption, value):
            label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    column.addWidget(caption)
    column.addWidget(value)
    return column
