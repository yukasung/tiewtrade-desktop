"""How a market reads on screen.

A market is two assets traded against each other, and an exchange writes it
as the pair it is: BTC/USDT. The API name for the same thing is BTCUSDT —
one word, no separator — and that is what the domain, the database and the
exchange's own requests carry. The screen is where it is read, so the screen
is where the two assets are told apart.

One place decides this, so a market cannot read one way in the setup form
and another in Position History.
"""

from tiewtrade.ui.money_text import QUOTE_ASSET


def pair_text(symbol: str) -> str:
    """The two assets of a market, separated.

    A symbol that does not end in the asset everything here is quoted in is
    left as it is rather than split at a guess.
    """
    if symbol.endswith(QUOTE_ASSET) and len(symbol) > len(QUOTE_ASSET):
        return f"{symbol[: -len(QUOTE_ASSET)]}/{QUOTE_ASSET}"
    return symbol
