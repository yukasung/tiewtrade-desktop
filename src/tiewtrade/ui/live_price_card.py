"""The price the market is at, kept on screen whatever the chart is showing.

The chart draws the current price only while it is following the candle
forming now: pan back a day and the line goes with the range it belonged to.
The price itself has not stopped arriving, so it is read here instead — a
card of its own, fed straight from the facts the feed publishes rather than
from the chart's view of them.
"""

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from tiewtrade.application.chart_data import ChartCandle
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.money_text import cent_price_text
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


class LivePriceCard(QFrame):
    """Names one market and the last price seen in it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("livePriceCard")
        self.pair = QLabel("")
        self.pair.setObjectName("eyebrow")
        self.price = QLabel(BLANK)
        self.price.setObjectName("livePrice")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(2)
        layout.addWidget(self.pair)
        layout.addWidget(self.price)
        self._stale = False
        self._market_type: MarketType | None = None

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
        self.setProperty("stale", stale)
        # The rule that dims reads the property off the card and names the
        # labels inside it, so the labels are the ones that have to be asked
        # for their colour again.
        style = self.style()
        for widget in (self, self.pair, self.price):
            style.unpolish(widget)
            style.polish(widget)

    @property
    def is_stale(self) -> bool:
        return self._stale

    def clear(self) -> None:
        self.set_stale(False)
        self.price.setText(BLANK)
        self.price.setStyleSheet("")
