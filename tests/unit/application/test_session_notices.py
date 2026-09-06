from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tiewtrade.application.session_notice import NoticeKind
from tiewtrade.application.session_notices import (
    SessionNoticeDelivery,
    SessionNoticeFilter,
    SessionNoticeHistoryItem,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000123")
NOTICE_ID = UUID("00000000-0000-0000-0000-0000000000e1")
RAISED_AT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)
SETTLED_AT = datetime(2026, 8, 26, 10, 5, 2, tzinfo=UTC)


def test_a_history_item_carries_what_a_screen_needs_and_nothing_it_must_ask_for() -> (
    None
):
    """A read model is judged by whether the screen can be built from it alone.

    Every field a Notification row shows — when, what, and how each channel
    took it — is here; nothing is worked out from a second lookup.
    """
    item = SessionNoticeHistoryItem(
        notice_id=NOTICE_ID,
        session_id=SESSION_ID,
        kind=NoticeKind.ENTRY_OPENED,
        message="Entry #2 opened",
        raised_at_utc=RAISED_AT,
        deliveries=(
            SessionNoticeDelivery(
                channel="line",
                outcome="delivered",
                attempts=1,
                settled_at_utc=SETTLED_AT,
                status=200,
            ),
        ),
    )

    assert item.message == "Entry #2 opened"
    assert item.deliveries[0].channel == "line"


def test_a_notice_with_no_channel_set_up_reads_with_no_deliveries() -> None:
    """A trading fact happened whether or not anyone was told about it."""
    item = SessionNoticeHistoryItem(
        notice_id=NOTICE_ID,
        session_id=SESSION_ID,
        kind=NoticeKind.BASKET_CLOSED,
        message="Position closed",
        raised_at_utc=RAISED_AT,
        deliveries=(),
    )
    assert item.deliveries == ()


@pytest.mark.parametrize(
    "create_filter",
    [
        lambda value: SessionNoticeFilter(from_utc=value),
        lambda value: SessionNoticeFilter(before_utc=value),
    ],
)
def test_session_notice_filter_requires_utc_bounds(
    create_filter: Callable[[datetime], SessionNoticeFilter],
) -> None:
    with pytest.raises(ValueError, match="UTC"):
        create_filter(datetime(2026, 1, 1))


def test_session_notice_filter_requires_ordered_time_bounds() -> None:
    boundary = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="before"):
        SessionNoticeFilter(from_utc=boundary, before_utc=boundary)
