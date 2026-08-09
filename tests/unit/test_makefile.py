from pathlib import Path


def test_makefile_exposes_documented_desktop_run_command() -> None:
    makefile = Path("Makefile").read_text()
    readme = Path("README.md").read_text()

    assert ".PHONY: run" in makefile
    assert "run:" in makefile
    assert "PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main" in makefile
    assert "make run" in readme
