from pathlib import Path


def test_makefile_exposes_documented_desktop_run_command() -> None:
    makefile = Path("Makefile").read_text()
    readme = Path("README.md").read_text()

    assert ".PHONY: run" in makefile
    assert "run:" in makefile
    assert "PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main" in makefile
    assert "make run" in readme


def test_makefile_exposes_a_development_database_command() -> None:
    """The command that keeps a checkout out of a trader's own history."""
    makefile = Path("Makefile").read_text()
    readme = Path("README.md").read_text()

    assert "run-dev:" in makefile
    assert "TIEWTRADE_DATABASE_PATH=.local/tiewtrade.sqlite3" in makefile
    assert "make run-dev" in readme
    assert "/.local/" in Path(".gitignore").read_text()


def test_the_delivered_bundle_ignores_the_development_database() -> None:
    """An exported variable must not move a trader's database out from under them.

    The frozen entry point takes no path, so the installed app answers to
    `default_database_path()` and nothing else.
    """
    entry_point = Path("packaging/entry_point.py").read_text()

    assert "run_desktop()" in entry_point
    assert "TIEWTRADE_DATABASE_PATH" not in entry_point
    assert "database_path_from_environment" not in entry_point
