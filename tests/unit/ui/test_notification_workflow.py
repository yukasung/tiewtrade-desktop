from datetime import UTC, date, datetime
from threading import Event
from uuid import UUID, uuid4

from PySide6.QtCore import QThreadPool
from pytestqt.qtbot import QtBot

from tiewtrade.application.session_notice import NoticeKind
from tiewtrade.application.session_notices import (
    SessionNoticeFilter,
    SessionNoticeHistoryItem,
)
from tiewtrade.ui.date_range_filter import DateRangeValues, default_date_range_values
from tiewtrade.ui.display_time import display_today
from tiewtrade.ui.notification_page import NotificationPage
from tiewtrade.ui.notification_presenter import session_notice_filter
from tiewtrade.ui.notification_workflow import NotificationWorkflow

SESSION_ID = uuid4()
RAISED_AT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)


def _item(message: str = "Entry #2 opened") -> SessionNoticeHistoryItem:
    return SessionNoticeHistoryItem(
        notice_id=uuid4(),
        session_id=SESSION_ID,
        kind=NoticeKind.ENTRY_OPENED,
        message=message,
        raised_at_utc=RAISED_AT,
        deliveries=(),
    )


def _pool() -> QThreadPool:
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    return pool


def _today_filter() -> SessionNoticeFilter:
    """The range the workflow opens on, which is today rather than everything."""
    return session_notice_filter(default_date_range_values(display_today()))


def test_activating_with_no_session_shows_the_empty_state(qtbot: QtBot) -> None:
    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=lambda _session_id, _filters: (_item(),),
        thread_pool=_pool(),
    )
    workflow.attach(page)

    workflow.activate()

    qtbot.waitUntil(lambda: page.empty_state.text() == "No notifications yet")
    assert page.table.rowCount() == 0


def test_activating_with_a_session_reads_its_notices(qtbot: QtBot) -> None:
    def list_session_notices(
        session_id: UUID, _filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        return (_item(),) if session_id == SESSION_ID else ()

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)

    workflow.activate()

    qtbot.waitUntil(lambda: page.table.rowCount() == 1)


def test_no_notices_for_the_session_reads_as_empty(qtbot: QtBot) -> None:
    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=lambda _session_id, _filters: (), thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)

    workflow.activate()

    qtbot.waitUntil(lambda: page.empty_state.text() == "No notifications yet")


def test_a_read_that_raises_says_unavailable_and_offers_retry(qtbot: QtBot) -> None:
    def failing(
        _session_id: UUID, _filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        raise RuntimeError("sqlite failed at /private/tmp with api_key=secret")

    page = NotificationPage()
    qtbot.addWidget(page)
    page.show()
    workflow = NotificationWorkflow(list_session_notices=failing, thread_pool=_pool())
    workflow.attach(page)
    workflow.show_session(SESSION_ID)

    workflow.activate()

    qtbot.waitUntil(lambda: page.state_label.text() == "Notifications unavailable")
    qtbot.waitUntil(page.retry_button.isVisible)
    # A path leaked into the error must never reach the screen — sanitized,
    # not the exception's own text.
    assert "api_key" not in page.state_label.text()


def test_retrying_reads_again(qtbot: QtBot) -> None:
    calls: list[UUID] = []

    def list_session_notices(
        session_id: UUID, _filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        calls.append(session_id)
        return (_item(),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()
    qtbot.waitUntil(lambda: len(calls) == 1)

    page.retry_requested.emit()

    qtbot.waitUntil(lambda: len(calls) == 2)


def test_changing_the_session_while_active_reads_the_new_one(qtbot: QtBot) -> None:
    other_session = uuid4()
    calls: list[UUID] = []

    def list_session_notices(
        session_id: UUID, _filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        calls.append(session_id)
        return (_item(),) if session_id == other_session else ()

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()
    qtbot.waitUntil(lambda: SESSION_ID in calls)

    workflow.show_session(other_session)

    qtbot.waitUntil(lambda: page.table.rowCount() == 1)


def test_a_reply_from_a_session_no_longer_current_is_dropped(qtbot: QtBot) -> None:
    """A slow read for a session the trader has since left must not paint over it.

    Switching Sessions bumps a generation, and a reply tagged with the old one
    is discarded rather than shown as if it were still the answer to what is
    on screen now.
    """
    release = Event()
    other_session = uuid4()

    def slow_then_fast(
        session_id: UUID, _filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        if session_id == SESSION_ID:
            assert release.wait(2)
            return (_item("stale"),)
        return (_item("fresh"),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=slow_then_fast, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()

    workflow.show_session(other_session)
    qtbot.waitUntil(lambda: page.table.rowCount() == 1)
    release.set()

    qtbot.wait(200)
    item = page.table.item(0, page.headers.index("Notice"))
    assert item is not None
    assert item.text() == "fresh"


def test_closing_stops_a_reply_from_reaching_the_page(qtbot: QtBot) -> None:
    release = Event()

    def slow(
        _session_id: UUID, _filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        assert release.wait(2)
        return (_item(),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(list_session_notices=slow, thread_pool=_pool())
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()

    workflow.close()
    release.set()
    qtbot.wait(200)

    assert page.table.rowCount() == 0


def test_activating_reads_over_todays_range_by_default(qtbot: QtBot) -> None:
    requested: list[SessionNoticeFilter] = []

    def list_session_notices(
        _session_id: UUID, filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        requested.append(filters)
        return (_item(),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)

    workflow.activate()

    qtbot.waitUntil(lambda: requested == [_today_filter()])


def test_applying_a_filter_reads_the_new_range(qtbot: QtBot) -> None:
    requested: list[SessionNoticeFilter] = []

    def list_session_notices(
        _session_id: UUID, filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        requested.append(filters)
        return (_item(),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()
    qtbot.waitUntil(lambda: len(requested) == 1)

    workflow.apply_filters(DateRangeValues(from_date=date(2026, 1, 2)))

    qtbot.waitUntil(lambda: len(requested) == 2)
    assert requested[-1] == SessionNoticeFilter(
        from_utc=datetime(2026, 1, 1, 17, tzinfo=UTC)
    )


def test_resetting_the_filter_reads_todays_range_again(qtbot: QtBot) -> None:
    requested: list[SessionNoticeFilter] = []

    def list_session_notices(
        _session_id: UUID, filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        requested.append(filters)
        return (_item(),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()
    qtbot.waitUntil(lambda: len(requested) == 1)
    workflow.apply_filters(DateRangeValues(from_date=date(2026, 1, 2)))
    qtbot.waitUntil(lambda: len(requested) == 2)

    workflow.reset_filters()

    qtbot.waitUntil(lambda: len(requested) == 3)
    assert requested[-1] == _today_filter()


def test_an_invalid_range_is_reported_without_reading(qtbot: QtBot) -> None:
    requested: list[SessionNoticeFilter] = []

    def list_session_notices(
        _session_id: UUID, filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        requested.append(filters)
        return (_item(),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=list_session_notices, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()
    qtbot.waitUntil(lambda: len(requested) == 1)

    workflow.apply_filters(
        DateRangeValues(from_date=date(2026, 1, 2), to_date=date(2026, 1, 1))
    )

    qtbot.waitUntil(
        lambda: (
            page.filters.filter_error.text() == "From Date must not be after To Date"
        )
    )
    assert len(requested) == 1


def test_a_reply_for_a_range_no_longer_asked_for_is_dropped(qtbot: QtBot) -> None:
    """A slow read must not paint over a range the trader has since left."""
    release = Event()

    def slow_then_fast(
        _session_id: UUID, filters: SessionNoticeFilter
    ) -> tuple[SessionNoticeHistoryItem, ...]:
        if filters == _today_filter():
            assert release.wait(2)
            return (_item("stale"),)
        return (_item("fresh"),)

    page = NotificationPage()
    qtbot.addWidget(page)
    workflow = NotificationWorkflow(
        list_session_notices=slow_then_fast, thread_pool=_pool()
    )
    workflow.attach(page)
    workflow.show_session(SESSION_ID)
    workflow.activate()

    workflow.apply_filters(DateRangeValues(from_date=date(2026, 1, 2)))
    qtbot.waitUntil(lambda: page.table.rowCount() == 1)
    release.set()

    qtbot.wait(200)
    item = page.table.item(0, page.headers.index("Notice"))
    assert item is not None
    assert item.text() == "fresh"
