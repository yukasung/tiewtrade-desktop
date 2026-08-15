"""How an amount of money reads on screen.

Money is counted in thousands, so its digits are grouped in threes: a price
of 63,156.08 can be read at a glance where 63156.08 has to be counted. A
quantity is not money — it is counted in the asset itself, where a group
separator would only suggest a scale the number does not have — so it is
formatted elsewhere and left ungrouped.

One place decides this, so the same figure cannot read one way in the
Position tab and another in Trade History.
"""

from decimal import Decimal

QUOTE_ASSET = "USDT"


def price_text(value: Decimal) -> str:
    """A price as the market quotes it, at the precision it was quoted in."""
    return f"{value:,f}"


def money_text(value: Decimal) -> str:
    """An amount as it was recorded, down to the last place it carries.

    Fees and realised results are kept exactly, so rounding them for the
    screen would report a number the ledger does not hold.
    """
    if value == 0:
        return f"0.00 {QUOTE_ASSET}"
    return f"{value:,f} {QUOTE_ASSET}"


def money_to_cent(value: Decimal) -> str:
    """An amount to the cent, for figures that are re-read every candle.

    A live readout changes with the price, so trailing places that move on
    every tick are noise rather than information.
    """
    return f"{value:,.2f} {QUOTE_ASSET}"
