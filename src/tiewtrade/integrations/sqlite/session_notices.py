"""Where what the Bot said is kept, and what each chat service made of it.

Two tables, because one thing happened and each channel settled it separately:
`session_notices` is the event, `session_notice_deliveries` is how the sending
went, one row per channel. Reading them back together is what answers the
question the audit trail is for — what was the trader told, and did it reach
them.

Nothing here raises. Every other durable write in this application is
fail-closed, and this one is deliberately not: a Session that stopped trading
because its diary could not be written would be a Bot brought down by its own
record-keeping. What a failure costs is the record, which is the same rule the
push it describes is already under.
"""

import logging
import sqlite3
from datetime import datetime
from uuid import UUID

from tiewtrade.application.session_notice import NoticeKind
from tiewtrade.application.session_notices import (
    SessionNoticeDelivery,
    SessionNoticeHistoryItem,
)
from tiewtrade.integrations.notifications.notice_journal import (
    NoticeDelivery,
    RaisedNotice,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase

_logger = logging.getLogger(__name__)


class SQLiteSessionNotices:
    """Keeps every notice a Session raised, and how each channel took it."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def record_raised(self, notice: RaisedNotice) -> None:
        """Keep what was said, once, however many times it is offered.

        The id is worked out from the event itself, so two channels keeping
        the same notice — and a Runtime raising it again after a restart —
        settle on one row without either having to know about the other.
        """
        self._write(
            """
            INSERT INTO session_notices (
                notice_id, session_id, kind, message, raised_at_utc
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(notice_id) DO NOTHING
            """,
            (
                str(notice.notice_id),
                str(notice.session_id),
                notice.kind.value,
                notice.message,
                notice.raised_at_utc.isoformat(),
            ),
            said="notification.record_failed",
        )

    def record_delivery(self, delivery: NoticeDelivery) -> None:
        """Keep how one channel's sending of one notice ended.

        A channel settles a notice once, and it may say so more than once on
        the way — the last word is the one kept, because the record is what
        became of the message rather than a transcript of trying.
        """
        self._write(
            """
            INSERT INTO session_notice_deliveries (
                notice_id, channel, outcome, status, attempts, settled_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(notice_id, channel) DO UPDATE SET
                outcome = excluded.outcome,
                status = excluded.status,
                attempts = excluded.attempts,
                settled_at_utc = excluded.settled_at_utc
            """,
            (
                str(delivery.notice_id),
                delivery.channel,
                delivery.outcome,
                delivery.status,
                delivery.attempts,
                delivery.settled_at_utc.isoformat(),
            ),
            said="notification.delivery_record_failed",
        )

    def list_notices(self, session_id: UUID) -> tuple[SessionNoticeHistoryItem, ...]:
        """Everything this Session said, newest first, with its deliveries.

        Newest first because it is read to catch up: what the Bot did while
        nobody was watching reads backwards from now.
        """
        try:
            connection = self._database.connect()
        except sqlite3.Error:
            _logger.warning("notification.record_unreadable")
            return ()
        try:
            with connection:
                notices = connection.execute(
                    """
                    SELECT notice_id, session_id, kind, message, raised_at_utc
                    FROM session_notices
                    WHERE session_id = ?
                    ORDER BY raised_at_utc DESC, notice_id DESC
                    """,
                    (str(session_id),),
                ).fetchall()
                deliveries = connection.execute(
                    """
                    SELECT session_notice_deliveries.*
                    FROM session_notice_deliveries
                    JOIN session_notices
                      ON session_notices.notice_id
                       = session_notice_deliveries.notice_id
                    WHERE session_notices.session_id = ?
                    ORDER BY session_notice_deliveries.channel
                    """,
                    (str(session_id),),
                ).fetchall()
        except sqlite3.Error:
            _logger.warning("notification.record_unreadable")
            return ()
        finally:
            connection.close()
        by_notice: dict[str, list[SessionNoticeDelivery]] = {}
        for row in deliveries:
            by_notice.setdefault(row["notice_id"], []).append(_delivery(row))
        return tuple(
            _history_item(row, tuple(by_notice.get(row["notice_id"], ())))
            for row in notices
        )

    def _write(
        self, statement: str, parameters: tuple[object, ...], *, said: str
    ) -> None:
        try:
            connection = self._database.connect()
        except sqlite3.Error:
            _logger.warning(said)
            return
        try:
            with connection:
                connection.execute(statement, parameters)
        except sqlite3.Error:
            # Said without the row, which carries whatever the Bot was about
            # to tell the trader and is not this log's to repeat.
            _logger.warning(said)
        finally:
            connection.close()


def _history_item(
    row: sqlite3.Row, deliveries: tuple[SessionNoticeDelivery, ...]
) -> SessionNoticeHistoryItem:
    return SessionNoticeHistoryItem(
        notice_id=UUID(row["notice_id"]),
        session_id=UUID(row["session_id"]),
        kind=NoticeKind(row["kind"]),
        message=row["message"],
        raised_at_utc=datetime.fromisoformat(row["raised_at_utc"]),
        deliveries=deliveries,
    )


def _delivery(row: sqlite3.Row) -> SessionNoticeDelivery:
    return SessionNoticeDelivery(
        channel=row["channel"],
        outcome=row["outcome"],
        attempts=row["attempts"],
        settled_at_utc=datetime.fromisoformat(row["settled_at_utc"]),
        status=row["status"],
    )
