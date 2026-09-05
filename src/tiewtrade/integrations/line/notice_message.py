"""How one notice reads in a chat message.

A message is read on a phone, in a line or two, by someone who cannot see the
Position tab: it says which market it is about, what happened, and nothing it
cannot say for certain.

Every message is laid out in the same three blocks, separated by a blank
line, so a phone full of them can be read down the left edge: what happened
and in which market, then what that one kind of notice knows, then when. The
blank lines are what make the blocks legible at a glance — without them a
six-line message is a wall the eye has to parse a line at a time.

The middle block is said in the plainest words that still say it: a labelled
figure, or a whole sentence about what the Bot will do next.

Money reads to the cent, and every figure that is money carries the asset it
is counted in — a price no less than an amount. A quantity is not money; it
is counted in the asset itself and keeps the places it was filled at.

Instants stay UTC and say so. The app's own screens read the Bangkok wall
clock, but that reading belongs to `ui`, and a message is not a screen.
"""

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    BotErrorNotice,
    CooldownCancelledNotice,
    CooldownEndedNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    MarketFeedLostNotice,
    MarketFeedRestoredNotice,
    SessionMarket,
    SessionNotice,
)
from tiewtrade.trading.basket import BasketCloseReason

QUOTE_ASSET = "USDT"
_CENT = Decimal("0.01")

_CLOSE_REASONS = {
    BasketCloseReason.TAKE_PROFIT: "Take profit",
    BasketCloseReason.MANUAL: "Closed by you",
}


def notice_message(notice: SessionNotice) -> str:
    """The whole notice as one text message, whichever notice it is."""
    match notice:
        case EntryOpenedNotice():
            return entry_opened_message(notice)
        case BasketClosedNotice():
            return basket_closed_message(notice)
        case CooldownStartedNotice():
            return cooldown_started_message(notice)
        case CooldownEndedNotice():
            return cooldown_ended_message(notice)
        case CooldownCancelledNotice():
            return cooldown_cancelled_message(notice)
        case MarketFeedLostNotice():
            return market_feed_lost_message(notice)
        case MarketFeedRestoredNotice():
            return market_feed_restored_message(notice)
        case BotErrorNotice():
            return bot_error_message(notice)


def entry_opened_message(notice: EntryOpenedNotice) -> str:
    """One Entry filled: what it paid, and where it is heading."""
    body = [
        f"Price {_price(notice.price)}",
        f"Quantity {notice.quantity:f} (cost {_money(notice.notional)})",
    ]
    if notice.take_profit_price is not None:
        body.append(f"Take profit at {_price(notice.take_profit_price)}")
    return _message(
        f"Entry #{notice.entry_number} opened",
        notice.market,
        body,
        _at("Filled", notice.filled_at),
    )


def basket_closed_message(notice: BasketClosedNotice) -> str:
    """A Position let go: why it went, and what the whole round came to.

    The result is said as profit or loss rather than as a signed number,
    because that is the one line a message like this is read for.
    """
    return _message(
        "Position closed",
        notice.market,
        [
            f"{_CLOSE_REASONS[notice.close_reason]} · {_result(notice.realized_pnl)}",
            f"Entries {notice.entry_count}",
            f"Average entry price {_price(notice.average_entry_price)}",
            f"Exit price {_price(notice.exit_price)}",
        ],
        _at("Closed", notice.closed_at),
    )


def cooldown_started_message(notice: CooldownStartedNotice) -> str:
    """A Cooldown began, and what it will take to end it.

    A Cooldown that waits out days names the day it lifts on, because a
    trader reading this one has nothing to do about it and a trader reading
    the other one does.
    """
    waiting = (
        "The Bot will not open a new Entry until you cancel the Cooldown."
        if notice.ends_at is None
        else (
            f"The Bot will not open a new Entry until {_moment(notice.ends_at)},"
            " or until you cancel the Cooldown."
        )
    )
    return _message(
        "Cooldown started",
        notice.market,
        [
            f"{_pairs(notice.completed_pairs)} completed"
            f" · {_entries(notice.open_entry_count)} open",
            waiting,
        ],
        _at("Started", notice.started_at),
    )


def cooldown_ended_message(notice: CooldownEndedNotice) -> str:
    """A Cooldown ran out of days, which nobody pressed anything to do."""
    return _message(
        "Cooldown ended",
        notice.market,
        [
            f"{_pairs(notice.completed_pairs)} completed",
            "The Cooldown ran out of days on its own."
            " The Bot can open a new Entry again.",
        ],
        _at("Ended", notice.ended_at),
    )


def cooldown_cancelled_message(notice: CooldownCancelledNotice) -> str:
    """A Cooldown lifted, so the Bot may take another Entry."""
    return _message(
        "Cooldown cancelled",
        notice.market,
        [
            f"{_pairs(notice.completed_pairs)} completed",
            "The Bot can open a new Entry again.",
        ],
        _at("Cancelled", notice.cancelled_at),
    )


def market_feed_lost_message(notice: MarketFeedLostNotice) -> str:
    """The feed went away: the Bot is waiting, not trading, and not stopped."""
    return _message(
        "Market data lost",
        notice.market,
        [
            f"Reason: {notice.reason}",
            "The Bot is holding its Position and will not trade until the"
            " candles come back.",
        ],
        _at("Observed", notice.observed_at),
    )


def market_feed_restored_message(notice: MarketFeedRestoredNotice) -> str:
    """The feed came back, and candles are being decided on again."""
    return _message(
        "Market data restored",
        notice.market,
        ["The Bot is reading candles again."],
        _at("Observed", notice.observed_at),
    )


def bot_error_message(notice: BotErrorNotice) -> str:
    """The Bot stopped, and asks to be looked at."""
    return _message(
        "Bot stopped",
        notice.market,
        [
            f"Reason: {notice.reason}",
            "The Bot will not trade again until you start it.",
        ],
        _at("Observed", notice.observed_at),
    )


def _message(
    headline: str,
    market: SessionMarket,
    body: list[str],
    moment: str,
) -> str:
    """The three blocks of a message, with a blank line between each.

    The market goes under the headline rather than beside it, so the first
    thing read is what happened and the second is where — two short lines
    instead of one that wraps in the middle of a Symbol.
    """
    blocks = [f"{headline}\n{_market(market)}", "\n".join(body), moment]
    return "\n\n".join(blocks)


def _market(market: SessionMarket) -> str:
    return f"{market.symbol} {market.timeframe}"


def _at(label: str, moment: datetime) -> str:
    return f"{label} {_moment(moment)}"


def _moment(moment: datetime) -> str:
    return f"{moment:%Y-%m-%d %H:%M:%S} UTC"


def _pairs(count: int) -> str:
    """A count of Entry Pairs, said in the grammar the count asks for."""
    return f"{count} Entry Pair{'' if count == 1 else 's'}"


def _entries(count: int) -> str:
    """A count of Entries, said in the grammar the count asks for."""
    return f"{count} Entr{'y' if count == 1 else 'ies'}"


def _price(value: Decimal) -> str:
    """A price as money: grouped in thousands, to the cent, in the quote asset.

    A price is quoted at the Symbol's tick, and an average entry is a division
    that can run to as many places as the arithmetic gives it. Neither reads
    on a phone: a column of prices is only comparable at a glance when they
    all stop at the same place, so a message rounds them the way every screen
    in the app rounds an amount. The ledger still keeps every place.
    """
    return _money(value.quantize(_CENT, rounding=ROUND_HALF_EVEN))


def _money(value: Decimal) -> str:
    """An amount to the cent, the way every screen in the app rounds one."""
    return f"{value:,.2f} {QUOTE_ASSET}"


def _result(value: Decimal) -> str:
    """A round's result, named as the thing it is rather than as a sign."""
    if value < 0:
        return f"Loss {_money(-value)}"
    return f"Profit {_money(value)}"
