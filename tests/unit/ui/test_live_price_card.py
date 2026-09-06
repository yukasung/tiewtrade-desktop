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


def test_the_total_reads_beside_the_price_and_starts_blank(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    assert card.total_caption.text() == "Total P/L"
    assert card.total.text() == BLANK


def test_a_total_in_profit_reads_as_money_in_the_gain_colour(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_total_pnl(Decimal("1234.5"))

    assert card.total.text() == "1,234.50 USDT"
    assert POSITIVE_COLOR in card.total.styleSheet()


def test_a_total_in_loss_reads_in_the_loss_colour(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_total_pnl(Decimal("-12.5"))

    assert card.total.text() == "-12.50 USDT"
    assert NEGATIVE_COLOR in card.total.styleSheet()


def test_a_total_at_break_even_is_a_figure_rather_than_a_blank(qtbot: QtBot) -> None:
    """Zero across everything closed is a result, not a missing reading."""
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_total_pnl(Decimal("0"))

    assert card.total.text() == "0.00 USDT"
    assert card.total.styleSheet() == ""


def test_no_total_yet_reads_as_the_same_blank_the_price_uses(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_total_pnl(Decimal("10"))

    card.show_total_pnl(None)

    assert card.total.text() == BLANK
    assert card.total.styleSheet() == ""


def test_a_stale_feed_dims_the_total_and_hands_its_colour_back(qtbot: QtBot) -> None:
    """The unrealised half is priced off the feed that just stopped."""
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_total_pnl(Decimal("1234.5"))

    card.set_stale(True)

    assert card.total.text() == "1,234.50 USDT"
    assert card.total.styleSheet() == ""

    card.set_stale(False)

    assert POSITIVE_COLOR in card.total.styleSheet()


def test_clearing_the_price_leaves_the_total_alone(qtbot: QtBot) -> None:
    """A pair change moves the chart, not what the database has realised."""
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_total_pnl(Decimal("1234.5"))

    card.clear()

    assert card.total.text() == "1,234.50 USDT"
    assert POSITIVE_COLOR in card.total.styleSheet()


def test_the_drawdown_reads_beside_the_total_and_starts_blank(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    assert card.drawdown_caption.text() == "Drawdown"
    assert card.drawdown.text() == BLANK


def test_a_drawdown_reads_as_a_plain_money_figure(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_drawdown(Decimal("180"))

    assert card.drawdown.text() == "180.00 USDT"


def test_standing_at_the_peak_reads_as_zero_not_a_blank(qtbot: QtBot) -> None:
    """No distance from the peak is a result, not a missing reading."""
    card = LivePriceCard()
    qtbot.addWidget(card)

    card.show_drawdown(Decimal("0"))

    assert card.drawdown.text() == "0.00 USDT"


def test_no_drawdown_yet_reads_as_a_blank(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_drawdown(Decimal("180"))

    card.show_drawdown(None)

    assert card.drawdown.text() == BLANK


def test_clearing_the_price_leaves_the_drawdown_alone(qtbot: QtBot) -> None:
    card = LivePriceCard()
    qtbot.addWidget(card)
    card.show_drawdown(Decimal("180"))

    card.clear()

    assert card.drawdown.text() == "180.00 USDT"


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
