"""What a Session said, in the shape a screen reads it back in.

The read model, not the write path: `session_notice.py` states what a notice
is while the Bot is raising it, and this states what became of one after it
was kept — one item per event, with what every channel that was set up made
of it. `application` owns the shape because it is what `ui` is allowed to
depend on; the SQLite journal that fills it in is `integrations/sqlite`'s.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from tiewtrade.application.session_notice import NoticeKind


def _require_utc(value: datetime | None, field: str) -> None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field} must use UTC")


@dataclass(frozen=True, slots=True)
class SessionNoticeFilter:
    """The window a trader asked the Notification tab to read over."""

    from_utc: datetime | None = None
    before_utc: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.from_utc, "from_utc")
        _require_utc(self.before_utc, "before_utc")
        if (
            self.from_utc is not None
            and self.before_utc is not None
            and self.from_utc >= self.before_utc
        ):
            raise ValueError("from_utc must be before before_utc")


@dataclass(frozen=True, slots=True)
class SessionNoticeDelivery:
    """What one chat channel made of one notice, once it had settled."""

    channel: str
    outcome: str
    attempts: int
    settled_at_utc: datetime
    status: int | None = None


@dataclass(frozen=True, slots=True)
class SessionNoticeHistoryItem:
    """One thing the Bot said, and what each channel made of it."""

    notice_id: UUID
    session_id: UUID
    kind: NoticeKind
    message: str
    raised_at_utc: datetime
    deliveries: tuple[SessionNoticeDelivery, ...]


ListSessionNotices = Callable[
    [UUID, SessionNoticeFilter], tuple[SessionNoticeHistoryItem, ...]
]
