from decimal import Decimal

from tiewtrade.ui.money_text import money_text, money_to_cent, price_text


def test_a_price_is_grouped_at_the_precision_it_was_quoted_in() -> None:
    assert price_text(Decimal("63156.08")) == "63,156.08"
    assert price_text(Decimal("66000.1250")) == "66,000.1250"
    # Below a thousand there is nothing to group.
    assert price_text(Decimal("99.90")) == "99.90"


def test_an_amount_keeps_every_place_the_ledger_holds() -> None:
    assert money_text(Decimal("2273.69035202")) == "2,273.69035202 USDT"
    assert money_text(Decimal("62.3220702")) == "62.3220702 USDT"
    assert money_text(Decimal("0")) == "0.00 USDT"


def test_a_live_amount_reads_to_the_cent() -> None:
    assert money_to_cent(Decimal("757.8744")) == "757.87 USDT"
    assert money_to_cent(Decimal("29999.9492")) == "29,999.95 USDT"
    assert money_to_cent(Decimal("-0.0512")) == "-0.05 USDT"


def test_a_negative_amount_groups_the_digits_not_the_sign() -> None:
    assert price_text(Decimal("-1234.5")) == "-1,234.5"
    assert money_text(Decimal("-2273.69")) == "-2,273.69 USDT"
