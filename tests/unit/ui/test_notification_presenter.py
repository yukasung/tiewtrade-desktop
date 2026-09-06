from datetime import UTC, datetime
from uuid import uuid4

from tiewtrade.application.session_notice import NoticeKind
from tiewtrade.application.session_notices import (
    SessionNoticeDelivery,
    SessionNoticeHistoryItem,
)
from tiewtrade.ui.notification_presenter import notice_delivery_text, notice_headline

SESSION_ID = uuid4()
RAISED_AT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)
SETTLED_AT = datetime(2026, 8, 26, 10, 5, 2, tzinfo=UTC)


def _item(
    message: str, deliveries: tuple[SessionNoticeDelivery, ...]
) -> SessionNoticeHistoryItem:
    return SessionNoticeHistoryItem(
        notice_id=uuid4(),
        session_id=SESSION_ID,
        kind=NoticeKind.ENTRY_OPENED,
        message=message,
        raised_at_utc=RAISED_AT,
        deliveries=deliveries,
    )


def test_the_headline_is_the_first_line_a_trader_would_read_on_their_phone() -> None:
    """The row shows what a message opens with, not its whole body.

    A table cell is one line; a chat message is several, and the first is
    the one written to be read at a glance.
    """
    item = _item("Entry #2 opened\nBTCUSDT · 5m\n\nPrice 64,120.50", ())

    assert notice_headline(item) == "Entry #2 opened"


def test_no_channel_set_up_reads_as_no_channel_set_up() -> None:
    item = _item("Entry #2 opened", ())

    assert notice_delivery_text(item) == "No chat channel set up"


def test_a_delivered_channel_reads_as_delivered() -> None:
    item = _item(
        "Entry #2 opened",
        (
            SessionNoticeDelivery(
                channel="line",
                outcome="delivered",
                attempts=1,
                settled_at_utc=SETTLED_AT,
                status=200,
            ),
        ),
    )

    assert notice_delivery_text(item) == "LINE: Delivered"


def test_a_rejected_channel_names_the_status_it_was_refused_with() -> None:
    item = _item(
        "Entry #2 opened",
        (
            SessionNoticeDelivery(
                channel="telegram",
                outcome="rejected",
                attempts=1,
                settled_at_utc=SETTLED_AT,
                status=403,
            ),
        ),
    )

    assert notice_delivery_text(item) == "Telegram: Rejected (403)"


def test_a_channel_that_never_answered_reads_as_not_answered() -> None:
    item = _item(
        "Entry #2 opened",
        (
            SessionNoticeDelivery(
                channel="line",
                outcome="retryable",
                attempts=2,
                settled_at_utc=SETTLED_AT,
                status=None,
            ),
        ),
    )

    assert notice_delivery_text(item) == "LINE: Not answered"


def test_two_channels_read_in_the_order_the_row_carries_them() -> None:
    item = _item(
        "Entry #2 opened",
        (
            SessionNoticeDelivery(
                channel="line",
                outcome="delivered",
                attempts=1,
                settled_at_utc=SETTLED_AT,
                status=200,
            ),
            SessionNoticeDelivery(
                channel="telegram",
                outcome="rejected",
                attempts=1,
                settled_at_utc=SETTLED_AT,
                status=403,
            ),
        ),
    )

    assert notice_delivery_text(item) == "LINE: Delivered · Telegram: Rejected (403)"
