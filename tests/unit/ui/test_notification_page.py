from datetime import UTC, datetime
from uuid import uuid4

from PySide6.QtWidgets import QTableWidgetItem
from pytestqt.qtbot import QtBot

from tiewtrade.application.session_notice import NoticeKind
from tiewtrade.application.session_notices import (
    SessionNoticeDelivery,
    SessionNoticeHistoryItem,
)
from tiewtrade.ui.notification_page import NotificationPage

SESSION_ID = uuid4()
RAISED_AT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)


def _item(
    *,
    message: str = "Entry #2 opened",
    deliveries: tuple[SessionNoticeDelivery, ...] = (),
) -> SessionNoticeHistoryItem:
    return SessionNoticeHistoryItem(
        notice_id=uuid4(),
        session_id=SESSION_ID,
        kind=NoticeKind.ENTRY_OPENED,
        message=message,
        raised_at_utc=RAISED_AT,
        deliveries=deliveries,
    )


def _cell(widget: NotificationPage, header: str, row: int = 0) -> QTableWidgetItem:
    item = widget.table.item(row, widget.headers.index(header))
    assert item is not None
    return item


def test_starts_with_no_rows_and_the_empty_message_showing(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)

    assert widget.table.rowCount() == 0


def test_each_notice_is_its_own_row_newest_first(qtbot: QtBot) -> None:
    older = _item(message="Entry #1 opened")
    newer = _item(message="Entry #2 opened")

    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show_items((newer, older))

    assert widget.table.rowCount() == 2
    assert _cell(widget, "Notice", 0).text() == "Entry #2 opened"
    assert _cell(widget, "Notice", 1).text() == "Entry #1 opened"


def test_a_row_shows_when_it_happened_and_how_delivery_went(qtbot: QtBot) -> None:
    item = _item(
        deliveries=(
            SessionNoticeDelivery(
                channel="line",
                outcome="delivered",
                attempts=1,
                settled_at_utc=RAISED_AT,
                status=200,
            ),
        )
    )

    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show_items((item,))

    assert _cell(widget, "Date").text() == "2026-08-26 17:05:00"
    assert _cell(widget, "Delivery").text() == "LINE: Delivered"


def test_no_notices_shows_the_empty_state_message(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show()

    widget.show_empty("No notifications yet")

    assert widget.table.rowCount() == 0
    qtbot.waitUntil(widget.empty_state.isVisible)
    assert widget.empty_state.text() == "No notifications yet"


def test_showing_items_after_empty_clears_the_empty_state(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show_empty("No notifications yet")

    widget.show_items((_item(),))

    assert not widget.empty_state.isVisible()
    assert widget.table.rowCount() == 1


def test_an_unavailable_read_says_so_without_touching_old_rows(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show_items((_item(),))

    widget.show_unavailable("Notifications are unavailable right now")

    assert widget.state_label.text() == "Notifications are unavailable right now"
    assert widget.table.rowCount() == 1


def test_an_unavailable_read_offers_a_way_to_try_again(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show()

    widget.show_unavailable("Notifications are unavailable right now")

    qtbot.waitUntil(widget.retry_button.isVisible)

    asked = []
    widget.retry_requested.connect(lambda: asked.append(True))
    widget.retry_button.click()
    assert asked == [True]


def test_the_retry_button_hides_once_a_read_lands(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)
    widget.show_unavailable("Notifications are unavailable right now")

    widget.show_items((_item(),))

    assert not widget.retry_button.isVisible()


def test_the_page_offers_the_same_filter_bar_as_trade_history(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)

    assert [chip.text() for chip in widget.filters.range_chips.values()] == [
        "1 Day",
        "1 Week",
        "1 Month",
        "3 Months",
    ]

    emitted: list[object] = []
    widget.apply_filters_requested.connect(emitted.append)
    widget.filters.search_button.click()

    assert len(emitted) == 1


def test_resetting_the_filter_bar_asks_the_page_to_reset(qtbot: QtBot) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)

    asked = []
    widget.reset_requested.connect(lambda: asked.append(True))
    widget.filters.reset_button.click()

    assert asked == [True]


def test_a_filter_error_shows_on_the_bar_and_clears_on_a_fresh_result(
    qtbot: QtBot,
) -> None:
    widget = NotificationPage()
    qtbot.addWidget(widget)

    widget.show_filter_error("From Date must not be after To Date")

    assert widget.filters.filter_error.text() == "From Date must not be after To Date"

    widget.show_items((_item(),))

    assert widget.filters.filter_error.text() == ""
