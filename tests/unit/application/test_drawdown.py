from decimal import Decimal

from tiewtrade.application.drawdown import current_drawdown


def test_no_answer_when_the_total_is_not_known_yet() -> None:
    """A total that has not been read yet has no distance from a peak either."""
    assert current_drawdown(high_water_mark=Decimal("0"), current_total=None) is None


def test_no_answer_when_the_peak_is_not_known_yet() -> None:
    assert current_drawdown(high_water_mark=None, current_total=Decimal("120")) is None


def test_standing_at_the_peak_reads_as_no_drawdown() -> None:
    assert current_drawdown(
        high_water_mark=Decimal("500"), current_total=Decimal("500")
    ) == Decimal("0")


def test_below_the_peak_reads_as_the_distance_back_to_it() -> None:
    assert current_drawdown(
        high_water_mark=Decimal("500"), current_total=Decimal("320")
    ) == Decimal("180")


def test_a_total_above_the_stored_peak_is_itself_the_peak() -> None:
    """The stored figure lags a live tick that has already gone higher.

    A background write settles the stored high-water mark a moment later; the
    read here must not report a drawdown against a peak the total has already
    passed.
    """
    assert current_drawdown(
        high_water_mark=Decimal("500"), current_total=Decimal("650")
    ) == Decimal("0")


def test_a_loss_from_the_start_is_still_read_against_a_zero_peak() -> None:
    """An installation that has only ever lost still has a peak: its start."""
    assert current_drawdown(
        high_water_mark=Decimal("0"), current_total=Decimal("-40")
    ) == Decimal("40")
