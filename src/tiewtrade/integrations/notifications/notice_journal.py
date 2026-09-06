"""What is kept of a notice once it has been said, and how it is asked for.

A notice is the one thing the Bot does that leaves the machine, and until now
it left no trace behind it: what the trader was told, and whether it actually
got to them, lived in the log and nowhere else. PRODUCT.md 14 counts a
Notification among the records SQLite keeps, and the audit trail it belongs to
is the one that has to answer what the Bot did and how the sending went.

The contract is stated here, in the module that uses it, and implemented by
whatever is keeping records — the queue and the transports go on knowing
nothing about a database.

Two records rather than one, because they are settled at different moments and
by different numbers of people: one thing happened, and each chat service made
of it whatever it made of it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from tiewtrade.application.session_notice import (
    NoticeKind,
    SessionNotice,
    notice_kind,
    notice_moment,
)

_IDENTITY_NAMESPACE = uuid5(NAMESPACE_URL, "https://tiewtrade.local/session-notice")


@dataclass(frozen=True, slots=True)
class RaisedNotice:
    """One thing the Bot said, as the record of it reads."""

    notice_id: UUID
    session_id: UUID
    kind: NoticeKind
    message: str
    raised_at_utc: datetime


@dataclass(frozen=True, slots=True)
class NoticeDelivery:
    """What one chat service made of one notice, once it had settled.

    `status` is what the service answered with, and there is none when it
    never answered. `attempts` is how many pushes it took to get here, which
    is what tells a service having a bad minute from one that refused.
    """

    notice_id: UUID
    channel: str
    outcome: str
    attempts: int
    settled_at_utc: datetime
    status: int | None = None


class NoticeJournal(Protocol):
    """Keeps what was said and how the sending went.

    Neither call may raise: a record that could not be kept costs the record,
    never the Session — the same rule the push itself is under.
    """

    def record_raised(self, notice: RaisedNotice) -> None: ...

    def record_delivery(self, delivery: NoticeDelivery) -> None: ...


def raised_notice(notice: SessionNotice, message: str) -> RaisedNotice:
    """The record of one notice, identified by what happened rather than when
    it was written down.

    Both chat services read the same notice and a restart can raise the same
    one again, so the identity is derived from the notice itself: the same
    event writes the same row whoever gets to it first, and the journal needs
    no lock to keep one event from being kept twice.
    """
    kind = notice_kind(notice)
    moment = notice_moment(notice)
    session_id = notice.market.session_id
    return RaisedNotice(
        notice_id=uuid5(
            _IDENTITY_NAMESPACE,
            "|".join((str(session_id), kind.value, moment.isoformat(), message)),
        ),
        session_id=session_id,
        kind=kind,
        message=message,
        raised_at_utc=moment,
    )
