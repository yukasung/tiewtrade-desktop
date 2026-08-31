from datetime import UTC, datetime
from decimal import Decimal

from pytestqt.qtbot import QtBot

from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.session_config import MarketType
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.live_price_card import LivePriceCard
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


def test_card_starts_without_a_price_to_read(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    assert card.price.text() == BLANK
    assert card.pair.text() == ""


def test_a_rising_candle_reads_as_a_grouped_price_in_the_gain_colour(
    qtbot: QtBot,
) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_price(candle(open_price="77682.00", close_price="78355.24"))

    assert card.price.text() == "78,355.24"
    assert POSITIVE_COLOR in card.price.styleSheet()


def test_a_price_reads_to_the_cent_whatever_precision_it_was_quoted_in(
    qtbot: QtBot,
) -> None:
    """Binance quotes eight places, and the card is read at a glance."""
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_price(candle(close_price="78355.24000000"))

    assert card.price.text() == "78,355.24"

    card.show_price(candle(close_price="78355.246"))

    assert card.price.text() == "78,355.25"


def test_a_falling_candle_reads_in_the_loss_colour(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_price(candle(open_price="78355.24", close_price="77682.00"))

    assert card.price.text() == "77,682.00"
    assert NEGATIVE_COLOR in card.price.styleSheet()


def test_a_price_names_the_pair_it_was_quoted_in(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_price(candle())

    assert card.pair.text() == "BTCUSDT · 5m"


def test_the_pair_can_be_named_before_the_first_price_arrives(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_pair("ETHUSDT", "15m")

    assert card.pair.text() == "ETHUSDT · 15m"
    assert card.price.text() == BLANK


def test_the_market_type_reads_beside_the_pair(qtbot: QtBot) -> None:
    """Which market the price is from, not only which pair."""
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_market_type(MarketType.SPOT)
    card.show_price(candle())

    assert card.pair.text() == "BTCUSDT · 5m · Spot"


def test_the_market_type_outlives_a_change_of_pair(qtbot: QtBot) -> None:
    """A Session is pinned to one Market Type; only the pair is chosen."""
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_market_type(MarketType.SPOT)

    card.show_pair("ETHUSDT", "15m")

    assert card.pair.text() == "ETHUSDT · 15m · Spot"


def test_a_stale_feed_keeps_the_last_price_and_the_next_one_clears_it(
    qtbot: QtBot,
) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_price(candle(close_price="78355.24"))

    card.set_stale(True)

    assert card.price.text() == "78,355.24"
    assert card.is_stale
    assert card.price.styleSheet() == ""

    card.show_price(candle(close_price="78400.00"))

    assert not card.is_stale
    assert card.price.text() == "78,400.00"


def test_clearing_takes_the_price_back_to_a_blank(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_price(candle())
    card.set_stale(True)

    card.clear()

    assert card.price.text() == BLANK
    assert card.price.styleSheet() == ""
    assert not card.is_stale


def candle(
    *,
    open_price: str = "77682.00",
    close_price: str = "78355.24",
) -> Candle:
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        open=open_value,
        high=max(open_value, close_value),
        low=min(open_value, close_value),
        close=close_value,
    )
