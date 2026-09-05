from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar


class CooldownMode(StrEnum):
    OFF = "off"
    DURATION = "duration"
    UNTIL_CANCELLED = "until_cancelled"


@dataclass(frozen=True, slots=True)
class CooldownPolicy:
    """How a Cooldown ends, chosen once and pinned for the Session.

    A Cooldown that waits is measured against the time the market gave the
    Fill that completed the Pair, never against the machine's clock — the
    caller hands in the instant to answer as of, so replaying the same candles
    answers the same way every time.
    """

    MINIMUM_DAYS: ClassVar[int] = 1
    MAXIMUM_DAYS: ClassVar[int] = 30

    mode: CooldownMode
    days: int | None = None

    def __post_init__(self) -> None:
        if self.mode is not CooldownMode.DURATION:
            if self.days is not None:
                raise ValueError("days belongs to a Cooldown that waits, and no other")
            return
        if self.days is None:
            raise ValueError("a Cooldown that waits must say how many days")
        if isinstance(self.days, bool) or not isinstance(self.days, int):
            raise ValueError("days must be an integer")
        if not self.MINIMUM_DAYS <= self.days <= self.MAXIMUM_DAYS:
            raise ValueError(
                f"days must be between {self.MINIMUM_DAYS} and {self.MAXIMUM_DAYS}"
            )

    @classmethod
    def off(cls) -> "CooldownPolicy":
        return cls(mode=CooldownMode.OFF)

    @classmethod
    def until_cancelled(cls) -> "CooldownPolicy":
        return cls(mode=CooldownMode.UNTIL_CANCELLED)

    @classmethod
    def for_days(cls, days: int) -> "CooldownPolicy":
        return cls(mode=CooldownMode.DURATION, days=days)

    def ends_at(self, *, completed_at: datetime) -> datetime | None:
        """When the Cooldown this Pair started lifts on its own, if it ever does."""
        if self.days is None:
            return None
        return completed_at + timedelta(days=self.days)

    def has_lapsed(self, *, completed_at: datetime, as_of: datetime) -> bool:
        ends_at = self.ends_at(completed_at=completed_at)
        return ends_at is not None and as_of >= ends_at
