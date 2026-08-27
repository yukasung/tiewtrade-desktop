"""How one notice reads in a chat message.

A message is read on a phone, in a line or two, by someone who cannot see the
Position tab: it says which market it is about, what happened, and nothing it
cannot say for certain.

Every message opens with what happened and closes with when, so a phone full
of them can be read down the left edge. In between is only what that one kind
of notice knows.

Instants stay UTC and say so. The app's own screens read the Bangkok wall
clock, but that reading belongs to `ui`, and a message is not a screen.
"""

from datetime import datetime
from decimal import Decimal

from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    BotErrorNotice,
    CooldownCancelledNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    MarketFeedLostNotice,
    MarketFeedRestoredNotice,
    SessionMarket,
    SessionNotice,
)
from tiewtrade.trading.basket import BasketCloseReason

QUOTE_ASSET = "USDT"

_CLOSE_REASONS = {
    BasketCloseReason.TAKE_PROFIT: "Take profit",
    BasketCloseReason.STOP_LOSS: "Stop loss",
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
    lines = [
        f"Entry #{notice.entry_number} opened — {_market(notice.market)}",
        f"Price {_price(notice.price)}",
        f"Quantity {notice.quantity:f} ({_money(notice.notional)})",
    ]
    if notice.take_profit_price is not None:
        lines.append(f"Take profit {_price(notice.take_profit_price)}")
    lines.append(_at("Filled", notice.filled_at))
    return "\n".join(lines)


def basket_closed_message(notice: BasketClosedNotice) -> str:
    """A Position let go: why it went, and what the whole round came to.

    The result is said as profit or loss rather than as a signed number,
    because that is the one line a message like this is read for.
    """
    return "\n".join(
        [
            f"Position closed — {_market(notice.market)}",
            f"{_CLOSE_REASONS[notice.close_reason]} · {_result(notice.realized_pnl)}",
            f"Entries {notice.entry_count}",
            f"Average entry {_price(notice.average_entry_price)}",
            f"Exit {_price(notice.exit_price)}",
            _at("Closed", notice.closed_at),
        ]
    )


def cooldown_started_message(notice: CooldownStartedNotice) -> str:
    """A Cooldown began, which nothing but the trader will end."""
    return "\n".join(
        [
            f"Cooldown started — {_market(notice.market)}",
            f"Entry Pair {notice.completed_pairs} completed"
            f" · {notice.open_entry_count} Entries open",
            "No further Entry opens until you cancel the Cooldown.",
            _at("Started", notice.started_at),
        ]
    )


def cooldown_cancelled_message(notice: CooldownCancelledNotice) -> str:
    """A Cooldown lifted, so the Bot may take another Entry."""
    return "\n".join(
        [
            f"Cooldown cancelled — {_market(notice.market)}",
            f"Entry Pair {notice.completed_pairs} is no longer holding the gate.",
            _at("Cancelled", notice.cancelled_at),
        ]
    )


def market_feed_lost_message(notice: MarketFeedLostNotice) -> str:
    """The feed went away: the Bot is waiting, not trading, and not stopped."""
    return "\n".join(
        [
            f"Market data lost — {_market(notice.market)}",
            f"Reason {notice.reason}",
            "The Bot is holding its Position and deciding nothing until"
            " candles arrive again.",
            _at("Observed", notice.observed_at),
        ]
    )


def market_feed_restored_message(notice: MarketFeedRestoredNotice) -> str:
    """The feed came back, and candles are being decided on again."""
    return "\n".join(
        [
            f"Market data restored — {_market(notice.market)}",
            "The Bot is reading candles again.",
            _at("Observed", notice.observed_at),
        ]
    )


def bot_error_message(notice: BotErrorNotice) -> str:
    """The Bot stopped, and asks to be looked at."""
    return "\n".join(
        [
            f"Bot stopped — {_market(notice.market)}",
            notice.reason,
            "It will decide nothing more until it is started again.",
            _at("Observed", notice.observed_at),
        ]
    )


def _market(market: SessionMarket) -> str:
    return f"{market.symbol} {market.timeframe}"


def _at(label: str, moment: datetime) -> str:
    return f"{label} {moment:%Y-%m-%d %H:%M:%S} UTC"


def _price(value: Decimal) -> str:
    """A price as the market quotes it, grouped in thousands to be read."""
    return f"{value:,f}"


def _money(value: Decimal) -> str:
    """An amount to the cent, the way every screen in the app rounds one."""
    return f"{value:,.2f} {QUOTE_ASSET}"


def _result(value: Decimal) -> str:
    """A round's result, named as the thing it is rather than as a sign."""
    if value < 0:
        return f"Loss {_money(-value)}"
    return f"Profit {_money(value)}"
