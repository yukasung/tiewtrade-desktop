from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tests.support.paper_session_setup import SESSION_ID, configured_spot_session
from tiewtrade.application.session_notice import NoticeKind
from tiewtrade.integrations.notifications.notice_journal import (
    NoticeDelivery,
    RaisedNotice,
)
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.session_notices import SQLiteSessionNotices

NOTICE_ID = UUID("00000000-0000-0000-0000-0000000000e1")
OTHER_NOTICE_ID = UUID("00000000-0000-0000-0000-0000000000e2")
RAISED_AT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)
SETTLED_AT = datetime(2026, 8, 26, 10, 5, 2, tzinfo=UTC)


def test_a_notice_is_read_back_exactly_as_it_was_said(tmp_path: Path) -> None:
    """The whole point of the record: a restart does not lose what was said."""
    database_path = tmp_path / "tiewtrade.sqlite3"
    journal = SQLiteSessionNotices(_session_database(tmp_path))

    journal.record_raised(_raised())

    reopened = SQLiteSessionNotices(SQLiteDatabase(database_path))
    kept = reopened.list_notices(SESSION_ID)
    assert len(kept) == 1
    item = kept[0]
    raised = _raised()
    assert item.notice_id == raised.notice_id
    assert item.session_id == raised.session_id
    assert item.kind == raised.kind
    assert item.message == raised.message
    assert item.raised_at_utc == raised.raised_at_utc
    assert item.deliveries == ()


def test_the_same_event_kept_twice_is_still_one_record(tmp_path: Path) -> None:
    """Both chat services keep the same notice, and a restart may raise it again.

    The row is keyed by what happened, so keeping it again is not an error and
    does not double the Session's history.
    """
    journal = SQLiteSessionNotices(_session_database(tmp_path))

    journal.record_raised(_raised())
    journal.record_raised(_raised())

    assert len(journal.list_notices(SESSION_ID)) == 1


def test_each_channel_says_for_itself_how_the_sending_went(tmp_path: Path) -> None:
    journal = SQLiteSessionNotices(_session_database(tmp_path))
    journal.record_raised(_raised())

    journal.record_delivery(
        NoticeDelivery(
            notice_id=NOTICE_ID,
            channel="line",
            outcome="delivered",
            attempts=2,
            settled_at_utc=SETTLED_AT,
            status=200,
        )
    )
    journal.record_delivery(
        NoticeDelivery(
            notice_id=NOTICE_ID,
            channel="telegram",
            outcome="rejected",
            attempts=1,
            settled_at_utc=SETTLED_AT,
            status=403,
        )
    )

    deliveries = journal.list_notices(SESSION_ID)[0].deliveries
    assert [(one.channel, one.outcome, one.status) for one in deliveries] == [
        ("line", "delivered", 200),
        ("telegram", "rejected", 403),
    ]
    # A service that could not answer has no status to keep, and the record
    # must not invent one to fill the column.
    assert all(one.attempts > 0 for one in deliveries)


def test_a_service_that_never_answered_keeps_no_status(tmp_path: Path) -> None:
    journal = SQLiteSessionNotices(_session_database(tmp_path))
    journal.record_raised(_raised())

    journal.record_delivery(
        NoticeDelivery(
            notice_id=NOTICE_ID,
            channel="line",
            outcome="retryable",
            attempts=2,
            settled_at_utc=SETTLED_AT,
        )
    )

    assert journal.list_notices(SESSION_ID)[0].deliveries[0].status is None


def test_a_second_word_from_one_channel_replaces_the_first(tmp_path: Path) -> None:
    """One channel settles a notice once; what it settled on is what is kept."""
    journal = SQLiteSessionNotices(_session_database(tmp_path))
    journal.record_raised(_raised())

    for outcome, status in (("retryable", None), ("delivered", 200)):
        journal.record_delivery(
            NoticeDelivery(
                notice_id=NOTICE_ID,
                channel="line",
                outcome=outcome,
                attempts=2,
                settled_at_utc=SETTLED_AT,
                status=status,
            )
        )

    deliveries = journal.list_notices(SESSION_ID)[0].deliveries
    assert len(deliveries) == 1
    assert deliveries[0].outcome == "delivered"


def test_notices_read_back_newest_first(tmp_path: Path) -> None:
    """Read the way a trader catches up: what happened last, first."""
    journal = SQLiteSessionNotices(_session_database(tmp_path))
    journal.record_raised(_raised())
    journal.record_raised(
        RaisedNotice(
            notice_id=OTHER_NOTICE_ID,
            session_id=SESSION_ID,
            kind=NoticeKind.BASKET_CLOSED,
            message="Position closed",
            raised_at_utc=datetime(2026, 8, 26, 11, tzinfo=UTC),
        )
    )

    kept = journal.list_notices(SESSION_ID)

    assert [one.kind for one in kept] == [
        NoticeKind.BASKET_CLOSED,
        NoticeKind.ENTRY_OPENED,
    ]


def test_a_journal_that_cannot_write_costs_the_record_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A Session must never stop because its diary could not be written.

    The push itself is under this rule already — a message that cannot go out
    is logged and dropped — and a record of that message is worth strictly
    less than the message was.
    """
    journal = SQLiteSessionNotices(SQLiteDatabase(tmp_path / "never-migrated.sqlite3"))

    journal.record_raised(_raised())
    journal.record_delivery(
        NoticeDelivery(
            notice_id=NOTICE_ID,
            channel="line",
            outcome="delivered",
            attempts=1,
            settled_at_utc=SETTLED_AT,
            status=200,
        )
    )

    assert journal.list_notices(SESSION_ID) == ()


def _raised() -> RaisedNotice:
    return RaisedNotice(
        notice_id=NOTICE_ID,
        session_id=SESSION_ID,
        kind=NoticeKind.ENTRY_OPENED,
        message="Entry #2 opened",
        raised_at_utc=RAISED_AT,
    )


def _session_database(tmp_path: Path) -> SQLiteDatabase:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    SQLiteActivePaperSessions(database).create(configured_spot_session())
    return database
