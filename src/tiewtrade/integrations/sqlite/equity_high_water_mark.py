"""The highest Total P/L this installation has ever reached.

One row for the whole installation, the same scope Total P/L already reads at
— every Session's realised result plus whatever the open Basket is worth now.
The figure is read for a screen to watch, never for a decision the Bot makes,
so a read or write that cannot reach the file costs the display and nothing
else: it answers with the safest thing it can say rather than raising into a
Runtime that owes it nothing.
"""

import logging
import sqlite3
from datetime import datetime
from decimal import Decimal

from tiewtrade.integrations.sqlite.database import SQLiteDatabase

_logger = logging.getLogger(__name__)
_ZERO = Decimal("0")
_ROW_ID = 1


class SQLiteEquityHighWaterMark:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def read(self) -> Decimal:
        """The peak so far, or the zero floor of an account that has not moved."""
        try:
            connection = self._database.connect()
        except sqlite3.Error:
            _logger.warning("equity_high_water_mark.unreadable")
            return _ZERO
        try:
            row = connection.execute(
                "SELECT high_water_mark FROM equity_high_water_mark WHERE id = ?",
                (_ROW_ID,),
            ).fetchone()
        except sqlite3.Error:
            _logger.warning("equity_high_water_mark.unreadable")
            return _ZERO
        finally:
            connection.close()
        return _ZERO if row is None else Decimal(row["high_water_mark"])

    def record_if_higher(self, value: Decimal, *, observed_at_utc: datetime) -> Decimal:
        """Raise the peak to `value` if it stands above it, and say what stands now.

        One transaction reads and writes the same row, so two totals arriving
        close together settle on the higher of the two rather than a race
        deciding it. A file that cannot be reached still answers with `value`:
        the total just computed is at least as good a peak as none at all.
        """
        try:
            connection = self._database.connect()
        except sqlite3.Error:
            _logger.warning("equity_high_water_mark.write_failed")
            return value
        try:
            with connection:
                row = connection.execute(
                    "SELECT high_water_mark FROM equity_high_water_mark WHERE id = ?",
                    (_ROW_ID,),
                ).fetchone()
                current = _ZERO if row is None else Decimal(row["high_water_mark"])
                if value <= current:
                    return current
                connection.execute(
                    """
                    INSERT INTO equity_high_water_mark (
                        id, high_water_mark, updated_at_utc
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        high_water_mark = excluded.high_water_mark,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (_ROW_ID, str(value), observed_at_utc.isoformat()),
                )
                return value
        except sqlite3.Error:
            _logger.warning("equity_high_water_mark.write_failed")
            return value
        finally:
            connection.close()
