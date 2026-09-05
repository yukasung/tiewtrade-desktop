from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class CooldownPolicy:
    """How long a Cooldown holds a Basket back, chosen once per Session.

    A Cooldown is a number of days or nothing at all, so that is the whole of
    it: `days` of None is a Session that never cools down. There is no third
    thing to name, and naming one would only be a second way to say no days.

    The waiting is measured against the time the market gave the Fill that
    completed the Pair, never against the machine's clock — the caller hands
    in the instant to answer as of, so replaying the same candles answers the
    same way every time.
    """

    MINIMUM_DAYS: ClassVar[int] = 1
    MAXIMUM_DAYS: ClassVar[int] = 30
    DEFAULT_DAYS: ClassVar[int] = MAXIMUM_DAYS

    days: int | None

    def __post_init__(self) -> None:
        if self.days is None:
            return
        if isinstance(self.days, bool) or not isinstance(self.days, int):
            raise ValueError("days must be an integer")
        if not self.MINIMUM_DAYS <= self.days <= self.MAXIMUM_DAYS:
            raise ValueError(
                f"days must be between {self.MINIMUM_DAYS} and {self.MAXIMUM_DAYS}"
            )

    @classmethod
    def off(cls) -> "CooldownPolicy":
        return cls(days=None)

    @classmethod
    def for_days(cls, days: int) -> "CooldownPolicy":
        return cls(days=days)

    @classmethod
    def default(cls) -> "CooldownPolicy":
        """What a Session gets when nobody chose: the longest wait there is.

        The default holds a Basket back the most rather than the least, so a
        Session assembled without a choice can only ever be more cautious than
        one a trader stated.
        """
        return cls(days=cls.DEFAULT_DAYS)

    @property
    def never_cools_down(self) -> bool:
        """Whether a completed Entry Pair closes the gate at all."""
        return self.days is None

    def ends_at(self, *, completed_at: datetime) -> datetime | None:
        """When the Cooldown this Pair started lifts, if one started at all."""
        if self.days is None:
            return None
        return completed_at + timedelta(days=self.days)

    def has_lapsed(self, *, completed_at: datetime, as_of: datetime) -> bool:
        ends_at = self.ends_at(completed_at=completed_at)
        return ends_at is not None and as_of >= ends_at
