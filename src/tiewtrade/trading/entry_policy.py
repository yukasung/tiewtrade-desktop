from dataclasses import dataclass, field

from tiewtrade.trading.cooldown_policy import CooldownPolicy


@dataclass(frozen=True, slots=True)
class EntryPolicy:
    max_entries: int
    # A Cooldown is what the Entry gate does between Pairs, so the policy that
    # says how one ends belongs with the policy that says how many Entries
    # there are. Defaulted, because a Session that names no Cooldown means the
    # one this product had before it could be chosen.
    cooldown: CooldownPolicy = field(default_factory=CooldownPolicy.until_cancelled)

    def __post_init__(self) -> None:
        if not 2 <= self.max_entries <= 20 or self.max_entries % 2:
            raise ValueError("max_entries must be an even number between 2 and 20")
