from datetime import date
from decimal import Decimal

from tiewtrade.application.trade_history import TradeFillHistoryItem, TradeHistoryFilter
from tiewtrade.trading.trade_history import BasketResult, FillSide
from tiewtrade.ui.blank_text import BLANK
from tiewtrade.ui.date_range_filter import (
    DateRangeValues,
    default_date_range_values,
    utc_bounds,
    validate_range,
)
from tiewtrade.ui.display_time import display_timestamp
from tiewtrade.ui.money_text import money_text, price_text, realized_text
from tiewtrade.ui.pair_text import pair_text
from tiewtrade.ui.sign_state import sign_colour
from tiewtrade.ui.table_column import CENTER, LEFT, RIGHT, Column
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


def default_filter_values(today: date) -> DateRangeValues:
    """What the filter asks for before anyone has touched it.

    Both the bar and the workflow read the default from here, because a
    filter bar that says one thing while the query asks another is wrong in
    a way nothing on screen would show.
    """
    return default_date_range_values(today)


def trade_history_filter(values: DateRangeValues) -> TradeHistoryFilter:
    validate_range(values)
    from_utc, before_utc = utc_bounds(values)
    return TradeHistoryFilter(from_utc=from_utc, before_utc=before_utc)


def _closed_text(basket: BasketResult) -> str:
    """When the round realised its result, or nothing while it still runs."""
    if basket.closed_at_utc is None:
        return BLANK
    return display_timestamp(basket.closed_at_utc)


BASKET_COLUMNS: tuple[Column[BasketResult], ...] = (
    # Two stamps, because a round is listed under the day it closed but read
    # for how long it ran. One "Date" column could only be one of the two, and
    # whichever it was would read as the other half the time.
    Column("Opened", LEFT, lambda basket: display_timestamp(basket.opened_at_utc)),
    Column("Closed", LEFT, _closed_text),
    Column("Market", CENTER, lambda basket: display_enum(basket.market_type.value)),
    Column("Pair", LEFT, lambda basket: pair_text(basket.symbol)),
    Column("Timeframe", CENTER, lambda basket: basket.timeframe),
    # How many Entries the round took, which is also part of why it ended: a
    # completed Entry Pair is what starts a Cooldown, and a Session out
    # of Entries never opens again. The Total alone cannot tell one large
    # Entry from four that scaled in.
    Column("Entries", CENTER, lambda basket: str(basket.entry_count)),
    # "Total" is what an exchange calls price × quantity, and what the Fills
    # table beside this one already calls it. One word for one idea.
    Column("Total", RIGHT, lambda basket: money_text(basket.invested_notional)),
    # A Basket row exists only once it has closed, so its result is always
    # final — unlike a Fill's, a Basket's zero is a real break-even, not a
    # trade that has not realised anything yet.
    Column(
        "Profit / Loss",
        RIGHT,
        lambda basket: money_text(basket.realized_pnl),
        lambda basket: sign_colour(basket.realized_pnl),
    ),
    # Mode and Status are on the record but off the screen: a Paper Bot writes
    # only Paper, and a round listed here is read by what it realised.
)

FILL_COLUMNS: tuple[Column[TradeFillHistoryItem], ...] = (
    Column("Date", LEFT, lambda item: display_timestamp(item.fill.filled_at_utc)),
    Column("Pair", LEFT, lambda item: pair_text(item.symbol)),
    Column(
        "Side",
        CENTER,
        lambda item: display_enum(item.fill.side.value),
        lambda item: _side_colour(item.fill.side),
    ),
    Column("Price", RIGHT, lambda item: price_text(item.fill.price)),
    Column("Quantity", RIGHT, lambda item: decimal_text(item.fill.quantity)),
    Column("Total", RIGHT, lambda item: money_text(item.fill.notional)),
    Column(
        "Profit / Loss",
        RIGHT,
        lambda item: realized_text(item.fill.realized_pnl),
        lambda item: sign_colour(item.fill.realized_pnl),
    ),
    # Source is on the record but off the screen: every Fill a Paper Bot makes
    # says the same thing, so the column reads the same on every row.
)


def _side_colour(side: FillSide) -> str:
    """Buying and selling read as the two directions a price can move."""
    return POSITIVE_COLOR if side is FillSide.BUY else NEGATIVE_COLOR


def decimal_text(value: Decimal) -> str:
    """A quantity, which is counted in the asset rather than in money."""
    if value == 0:
        return "0.00"
    return format(value, "f")


def display_enum(value: str) -> str:
    return value.replace("_", " ").title()
