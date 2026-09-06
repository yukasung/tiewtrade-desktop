from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from tiewtrade.application.session_notice import (
    EntryOpenedNotice,
    MarketFeedRestoredNotice,
    NoticeKind,
    SessionMarket,
)
from tiewtrade.integrations.notifications.notice_journal import raised_notice

SESSION_ID = UUID("00000000-0000-0000-0000-0000000000aa")
MARKET = SessionMarket(session_id=SESSION_ID, symbol="BTCUSDT", timeframe="5m")
MOMENT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)


def _entry(*, entry_number: int = 2) -> EntryOpenedNotice:
    return EntryOpenedNotice(
        market=MARKET,
        basket_id=UUID("00000000-0000-0000-0000-0000000000bb"),
        entry_number=entry_number,
        price=Decimal("64120.50"),
        quantity=Decimal("0.0039"),
        take_profit_price=Decimal("65000.00"),
        filled_at=MOMENT,
    )


def test_a_raised_notice_carries_what_the_record_keeps() -> None:
    record = raised_notice(_entry(), "Entry #2 opened")

    assert record.session_id == SESSION_ID
    assert record.kind is NoticeKind.ENTRY_OPENED
    assert record.message == "Entry #2 opened"
    # The Fill's own time, not the moment the row was written.
    assert record.raised_at_utc == MOMENT


def test_one_thing_that_happened_is_one_record_however_often_it_is_read() -> None:
    """Both channels read the same notice, and it is one event, not two.

    The identity is worked out from what happened rather than handed out at
    the door, so two chat services pushing the same notice — and a Runtime
    that raises it again after a restart — write one row between them.
    """
    first = raised_notice(_entry(), "Entry #2 opened")
    again = raised_notice(_entry(), "Entry #2 opened")

    assert first.notice_id == again.notice_id


def test_two_different_things_are_two_records() -> None:
    entry = raised_notice(_entry(), "Entry #2 opened")
    later_entry = raised_notice(_entry(entry_number=3), "Entry #3 opened")
    other_kind = raised_notice(
        MarketFeedRestoredNotice(market=MARKET, observed_at=MOMENT),
        "Market feed restored",
    )

    assert len({entry.notice_id, later_entry.notice_id, other_kind.notice_id}) == 3


def test_the_record_is_bound_to_the_session_that_raised_it() -> None:
    """Every trading record is owned by a session_id, and so is this one."""
    other_market = SessionMarket(session_id=uuid4(), symbol="BTCUSDT", timeframe="5m")
    mine = raised_notice(_entry(), "Entry #2 opened")
    theirs = raised_notice(
        MarketFeedRestoredNotice(market=other_market, observed_at=MOMENT),
        "Market feed restored",
    )

    assert mine.session_id != theirs.session_id
    assert mine.notice_id != theirs.notice_id
