from tiewtrade.trading.cooldown_policy import CooldownPolicy


def cooldown_text(policy: CooldownPolicy) -> str:
    """How long this Cooldown holds a Basket back, in one phrase.

    Written once so the choice a trader picks on the setup form and the
    Session the overview card states back are the same words — a Session that
    reads differently in two places reads as two Sessions.
    """
    if policy.days is None:
        return "Off"
    return f"{policy.days} day" if policy.days == 1 else f"{policy.days} days"
