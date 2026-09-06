"""How far the account stands below the best it has ever been.

PRODUCT.md 10 asks the screen to show Drawdown beside PnL, for a trader to
watch rather than for the system to act on — Internal Alpha enforces no Stop
Loss, no Drawdown limit, no Daily Loss limit. The figure is read against a
high-water mark of Total P/L: the highest that figure has ever reached, kept
in `integrations/sqlite/equity_high_water_mark.py`. This module only reads
the two numbers together — the peak is somebody else's to remember.
"""

from decimal import Decimal


def current_drawdown(
    *,
    high_water_mark: Decimal | None,
    current_total: Decimal | None,
) -> Decimal | None:
    """The gap back to the peak, or no answer while either side is unknown.

    A live tick can outrun the peak on record — the write that would raise it
    settles a moment later — so the peak is read as at least the total
    standing now, never a stale figure the total has already passed.
    """
    if high_water_mark is None or current_total is None:
        return None
    return max(high_water_mark, current_total) - current_total
