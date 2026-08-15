from decimal import Decimal

from tiewtrade.ui.money_text import money_text, price_text


def test_a_price_is_grouped_at_the_precision_it_was_quoted_in() -> None:
    assert price_text(Decimal("63156.08")) == "63,156.08"
    assert price_text(Decimal("66000.1250")) == "66,000.1250"
    # Below a thousand there is nothing to group.
    assert price_text(Decimal("99.90")) == "99.90"


def test_an_amount_reads_to_the_cent_however_many_places_it_carries() -> None:
    """The ledger keeps every place; a column of amounts stops at the cent."""
    assert money_text(Decimal("2273.69035202")) == "2,273.69 USDT"
    assert money_text(Decimal("62.3220702")) == "62.32 USDT"
    assert money_text(Decimal("29999.9492")) == "29,999.95 USDT"
    assert money_text(Decimal("-0.0512")) == "-0.05 USDT"


def test_an_amount_reads_the_cents_it_does_not_carry() -> None:
    """A whole number still stops where its column does, so digits line up."""
    assert money_text(Decimal("200")) == "200.00 USDT"
    assert money_text(Decimal("0.2")) == "0.20 USDT"
    assert money_text(Decimal("0")) == "0.00 USDT"


def test_a_negative_amount_groups_the_digits_not_the_sign() -> None:
    assert price_text(Decimal("-1234.5")) == "-1,234.5"
    assert money_text(Decimal("-2273.69")) == "-2,273.69 USDT"
