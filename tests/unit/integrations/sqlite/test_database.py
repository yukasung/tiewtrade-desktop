from pathlib import Path

import pytest

from tiewtrade.integrations.sqlite.database import (
    SQLiteDatabase,
    UnsupportedDatabaseSchemaError,
)


def test_connect_enables_wal_and_explicit_busy_timeout(tmp_path: Path) -> None:
    connection = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3").connect()
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert journal_mode == "wal"
    assert busy_timeout == 5_000


def test_reader_can_read_committed_snapshot_while_writer_is_active(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    writer = database.connect()
    reader = database.connect()
    try:
        writer.execute("CREATE TABLE concurrency_probe (value TEXT NOT NULL)")
        writer.commit()
        reader.execute("PRAGMA busy_timeout = 50")

        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO concurrency_probe (value) VALUES ('uncommitted')")

        rows = reader.execute(
            "SELECT value FROM concurrency_probe ORDER BY value"
        ).fetchall()
    finally:
        writer.rollback()
        reader.close()
        writer.close()

    assert rows == []


def test_migrate_reports_newer_schema_with_version_facts(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 15")

    with pytest.raises(UnsupportedDatabaseSchemaError) as caught:
        database.migrate()

    assert caught.value.database_version == 15
    assert caught.value.supported_version == 14


def test_migrate_creates_cooldown_waivers_table_on_a_fresh_database(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")

    database.migrate()

    with database.connect() as connection:
        connection.execute(
            "SELECT basket_id, waived_pair, waived_at_utc FROM paper_cooldown_waivers"
        )


def test_migrate_adds_cooldown_waivers_table_to_an_older_database(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 12")

    database.migrate()

    with database.connect() as connection:
        connection.execute(
            "SELECT basket_id, waived_pair, waived_at_utc FROM paper_cooldown_waivers"
        )


def test_migrate_replaces_a_v13_waiver_table_keyed_by_month(
    tmp_path: Path,
) -> None:
    """A month named when a Cooldown lapsed; nothing lapses now, so it goes."""
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE paper_cooldown_waivers (
                basket_id TEXT PRIMARY KEY,
                completed_pair_month INTEGER NOT NULL,
                waived_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO paper_cooldown_waivers "
            "(basket_id, completed_pair_month, waived_at_utc) "
            "VALUES ('basket', 24313, '2026-01-25T00:00:00+00:00')"
        )
        connection.execute("PRAGMA user_version = 13")

    database.migrate()

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT basket_id, waived_pair, waived_at_utc FROM paper_cooldown_waivers"
        ).fetchall()

    assert rows == []


def test_migrate_v5_rebuilds_lifecycle_constraint_for_blocked_state(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("CREATE TABLE bot_sessions (session_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO bot_sessions (session_id) VALUES ('session')")
        connection.execute(
            """
            CREATE TABLE paper_runtime_lifecycle (
                session_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('running', 'stopped')),
                observed_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO paper_runtime_lifecycle (session_id, state, observed_at_utc)
            VALUES ('session', 'stopped', '2026-08-08T00:00:00+00:00')
            """
        )
        connection.execute("PRAGMA user_version = 5")

    database.migrate()

    with database.connect() as connection:
        states = [
            row["state"]
            for row in connection.execute(
                "SELECT state FROM paper_runtime_lifecycle ORDER BY session_id"
            )
        ]
        connection.execute(
            """
            UPDATE paper_runtime_lifecycle
            SET state = 'blocked', observed_at_utc = '2026-08-08T00:01:00+00:00'
            WHERE session_id = 'session'
            """
        )

    assert states == ["stopped"]
