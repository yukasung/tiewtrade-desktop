"""How one filled Entry reads in a chat message.

A message is read on a phone, in a line or two, by someone who cannot see
the Position tab: it says which market, which Entry of the Basket, what it
paid, and where it is heading — and nothing it cannot say for certain.

Instants stay UTC and say so. The app's own screens read the Bangkok wall
clock, but that reading belongs to `ui`, and a message is not a screen.
"""

from decimal import Decimal

from tiewtrade.application.entry_opened_notice import EntryOpenedNotice

QUOTE_ASSET = "USDT"


def entry_opened_message(notice: EntryOpenedNotice) -> str:
    """The whole notice as one text message."""
    lines = [
        f"Entry #{notice.entry_number} opened — {notice.symbol} {notice.timeframe}",
        f"Price {_price(notice.price)}",
        f"Quantity {notice.quantity:f} ({_money(notice.notional)})",
    ]
    if notice.take_profit_price is not None:
        lines.append(f"Take profit {_price(notice.take_profit_price)}")
    lines.append(f"Filled {notice.filled_at:%Y-%m-%d %H:%M:%S} UTC")
    return "\n".join(lines)


def _price(value: Decimal) -> str:
    """A price as the market quotes it, grouped in thousands to be read."""
    return f"{value:,f}"


def _money(value: Decimal) -> str:
    """An amount to the cent, the way every screen in the app rounds one."""
    return f"{value:,.2f} {QUOTE_ASSET}"
