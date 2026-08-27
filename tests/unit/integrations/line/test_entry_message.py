from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from tiewtrade.application.entry_opened_notice import EntryOpenedNotice
from tiewtrade.integrations.line.entry_message import entry_opened_message


def _notice(
    *,
    entry_number: int = 2,
    price: Decimal = Decimal("64120.50"),
    quantity: Decimal = Decimal("0.0039"),
    take_profit_price: Decimal | None = Decimal("65000.00"),
) -> EntryOpenedNotice:
    return EntryOpenedNotice(
        session_id=uuid4(),
        symbol="BTCUSDT",
        timeframe="5m",
        basket_id=uuid4(),
        entry_number=entry_number,
        price=price,
        quantity=quantity,
        take_profit_price=take_profit_price,
        filled_at=datetime(2026, 8, 26, 10, 5, tzinfo=UTC),
    )


def test_entry_opened_message_reads_the_whole_entry() -> None:
    message = entry_opened_message(_notice())

    assert message.splitlines() == [
        "Entry #2 opened — BTCUSDT 5m",
        "Price 64,120.50",
        "Quantity 0.0039 (250.07 USDT)",
        "Take profit 65,000.00",
        "Filled 2026-08-26 10:05:00 UTC",
    ]


def test_entry_opened_message_says_nothing_of_a_take_profit_it_has_not_got() -> None:
    message = entry_opened_message(_notice(take_profit_price=None))

    assert "Take profit" not in message
    assert "Entry #2 opened — BTCUSDT 5m" in message


def test_entry_opened_message_leaves_a_quantity_ungrouped() -> None:
    message = entry_opened_message(
        _notice(price=Decimal("1.25"), quantity=Decimal("12345.6789"))
    )

    assert "Quantity 12345.6789 (15,432.10 USDT)" in message
