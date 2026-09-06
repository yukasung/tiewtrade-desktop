"""What a Session said, in the shape a screen reads it back in.

The read model, not the write path: `session_notice.py` states what a notice
is while the Bot is raising it, and this states what became of one after it
was kept — one item per event, with what every channel that was set up made
of it. `application` owns the shape because it is what `ui` is allowed to
depend on; the SQLite journal that fills it in is `integrations/sqlite`'s.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from tiewtrade.application.session_notice import NoticeKind


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


ListSessionNotices = Callable[[UUID], tuple[SessionNoticeHistoryItem, ...]]
