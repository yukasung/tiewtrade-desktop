"""The script PyInstaller freezes.

A bundle needs a script to start, and pointing it at ``desktop_main`` itself
would import that module twice — once as ``__main__`` and once under its real
name, giving the app two copies of every module-level object it owns. This
file exists only so the frozen entry point and the importable module stay
separate.
"""

from tiewtrade.desktop_main import run_desktop

if __name__ == "__main__":
    raise SystemExit(run_desktop())
