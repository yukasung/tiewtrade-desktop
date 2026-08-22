"""Undoing and letting go of a connection while an error is already on its way.

An adapter that is failing has an error worth reporting; a rollback or a close
that fails on the way out has none, and letting one raise would replace the
reason the caller is being told about with the tidying that followed it. Both
are therefore best-effort, and each adapter still raises its own error itself.

Only cleanup lives here. Committing and closing on the success path are not
best-effort — a close that fails there is the one thing standing between a
caller and the claim that the write is durable — so an adapter that must hear
about it keeps its own close.
"""

import sqlite3


def rollback_if_open(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        connection.rollback()
    except sqlite3.Error:
        pass


def close_if_open(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except sqlite3.Error:
        pass
