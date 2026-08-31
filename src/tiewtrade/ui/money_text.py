"""How an amount of money reads on screen.

Money is counted in thousands, so its digits are grouped in threes: a price
of 63,156.08 can be read at a glance where 63156.08 has to be counted. A
quantity is not money — it is counted in the asset itself, where a group
separator would only suggest a scale the number does not have — so it is
formatted elsewhere and left ungrouped.

Amounts read to the cent. The screen is where a figure is read, not where
it is kept: the ledger and the database still hold every place a fee or a
result carries, and a column of amounts is only comparable at a glance when
they all stop at the same place. A column of rounded rows therefore need
not add up to the rounded total to the last cent, which is the ordinary
arithmetic of any rounded table.

One place decides this, so the same figure cannot read one way in the
Position tab and another in Trade History.
"""

from decimal import Decimal

from tiewtrade.ui.blank_text import BLANK

QUOTE_ASSET = "USDT"


def price_text(value: Decimal) -> str:
    """A price as the market quotes it, at the precision it was quoted in."""
    return f"{value:,f}"


def cent_price_text(value: Decimal) -> str:
    """A price to the cent, for a screen that reads it at a glance.

    Binance quotes eight places and a price of 78,355.24000000 is six
    characters of nothing: the zeros are the quoting convention, not the
    precision the price was struck at. The chart's axis and the sidebar's
    live price are both read in passing, so they stop at the cent — the
    ledger and the database still keep every place.
    """
    return f"{value:,.2f}"


def money_text(value: Decimal) -> str:
    """An amount as it reads on screen: grouped, and to the cent."""
    return f"{value:,.2f} {QUOTE_ASSET}"


def realized_text(value: Decimal) -> str:
    """A realised result, or a dash where a trade has not produced one.

    An Entry realises nothing, so printing 0.00 USDT beside it reads as a
    trade that closed at break-even rather than one that has not closed.
    Exactly zero is the test, not zero to the cent: a result of 0.001 did
    happen, and rounding it away to a dash would hide that it did.
    """
    return BLANK if value == 0 else money_text(value)
