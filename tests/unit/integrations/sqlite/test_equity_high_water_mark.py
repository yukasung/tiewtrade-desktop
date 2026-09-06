from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.equity_high_water_mark import (
    SQLiteEquityHighWaterMark,
)

OBSERVED_AT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)
LATER = datetime(2026, 8, 26, 10, 10, tzinfo=UTC)


def test_a_fresh_installation_has_never_moved_from_zero(tmp_path: Path) -> None:
    """Nothing has been made or lost yet, so the peak so far is a standing still."""
    mark = SQLiteEquityHighWaterMark(_database(tmp_path))

    assert mark.read() == Decimal("0")


def test_a_higher_figure_raises_the_peak_and_is_read_back(tmp_path: Path) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    mark = SQLiteEquityHighWaterMark(_database(tmp_path))

    result = mark.record_if_higher(Decimal("500"), observed_at_utc=OBSERVED_AT)

    assert result == Decimal("500")
    reopened = SQLiteEquityHighWaterMark(SQLiteDatabase(database_path))
    assert reopened.read() == Decimal("500")


def test_a_lower_figure_leaves_the_peak_standing(tmp_path: Path) -> None:
    mark = SQLiteEquityHighWaterMark(_database(tmp_path))
    mark.record_if_higher(Decimal("500"), observed_at_utc=OBSERVED_AT)

    result = mark.record_if_higher(Decimal("300"), observed_at_utc=LATER)

    assert result == Decimal("500")
    assert mark.read() == Decimal("500")


def test_a_negative_figure_never_lowers_the_zero_floor(tmp_path: Path) -> None:
    """A fresh account's peak is zero even before its first loss settles in."""
    mark = SQLiteEquityHighWaterMark(_database(tmp_path))

    result = mark.record_if_higher(Decimal("-40"), observed_at_utc=OBSERVED_AT)

    assert result == Decimal("0")
    assert mark.read() == Decimal("0")


def test_an_equal_figure_is_kept_without_error(tmp_path: Path) -> None:
    mark = SQLiteEquityHighWaterMark(_database(tmp_path))
    mark.record_if_higher(Decimal("500"), observed_at_utc=OBSERVED_AT)

    result = mark.record_if_higher(Decimal("500"), observed_at_utc=LATER)

    assert result == Decimal("500")


def test_a_read_that_cannot_reach_the_file_reads_as_the_zero_floor(
    tmp_path: Path,
) -> None:
    """A figure nobody can act on is worth logging and reading as unmoved.

    This display costs a trader no more than a quiet screen if it cannot be
    read — never a stopped Bot, which is not this record's to cause.
    """
    mark = SQLiteEquityHighWaterMark(
        SQLiteDatabase(tmp_path / "never-migrated.sqlite3")
    )

    assert mark.read() == Decimal("0")


def test_a_write_that_cannot_reach_the_file_still_answers_with_the_value_given(
    tmp_path: Path,
) -> None:
    mark = SQLiteEquityHighWaterMark(
        SQLiteDatabase(tmp_path / "never-migrated.sqlite3")
    )

    result = mark.record_if_higher(Decimal("500"), observed_at_utc=OBSERVED_AT)

    assert result == Decimal("500")


def _database(tmp_path: Path) -> SQLiteDatabase:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    return database
