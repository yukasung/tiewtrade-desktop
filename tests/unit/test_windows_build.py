"""What a Windows build has to carry that a macOS one does not.

PyInstaller cannot cross-compile, so the Windows executable is built on a
Windows machine by `packaging/build_windows.ps1` from the same spec the macOS
bundle uses. These tests hold the two together: one spec that answers for the
platform it runs on, and a script that runs the same checks `make build` does
before it produces anything.
"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPOSITORY_ROOT / "packaging" / "tiewtrade.spec"
SCRIPT_PATH = REPOSITORY_ROOT / "packaging" / "build_windows.ps1"
README_PATH = REPOSITORY_ROOT / "README.md"


def _spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def _script_text() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_the_spec_names_the_keyring_backend_of_each_platform() -> None:
    """keyring finds backends through entry points PyInstaller cannot follow.

    Left out, the Windows Credential Locker is absent at runtime and every
    stored credential reads as "not set" without raising — LINE notifications
    would look switched off forever.
    """
    spec = _spec_text()

    assert '"keyring.backends.Windows"' in spec
    assert '"keyring.backends.macOS"' in spec


def test_the_spec_bundles_an_app_only_where_a_bundle_means_something() -> None:
    """`BUNDLE` is a macOS .app; on Windows the collected folder is the app."""
    spec = _spec_text()

    assert 'IS_MACOS = sys.platform == "darwin"' in spec
    assert "if IS_MACOS:" in spec


def test_the_windows_script_runs_the_repository_checklist_before_building() -> None:
    """A build made from code that does not pass is a build nobody can trust."""
    script = _script_text()

    assert '"-m", "pytest", "-q"' in script
    assert '"-m", "ruff", "check", "src", "tests"' in script
    assert '"-m", "ruff", "format", "--check", "src", "tests"' in script
    assert '"-m", "mypy"' in script
    assert '"-m", "tiewtrade.paper_replay_main"' in script
    assert "git diff --check" in script


def test_the_windows_script_stops_at_the_first_check_that_fails() -> None:
    """PowerShell carries on past a failed native command unless told not to.

    Without the exit code read back, a failing test run and a passing one
    produce the same .exe.
    """
    script = _script_text()

    assert '$ErrorActionPreference = "Stop"' in script
    assert "if ($LASTEXITCODE -ne 0) {" in script


def test_the_windows_script_builds_the_collected_folder_from_the_shared_spec() -> None:
    script = _script_text()

    assert ".venv\\Scripts\\python.exe" in script
    assert '"-m", "PyInstaller"' in script
    assert '"packaging\\tiewtrade.spec"' in script
    assert '"--distpath", "dist"' in script
    assert '"--workpath", "build"' in script
    assert '"dist\\TiewTrade\\TiewTrade.exe"' in script


def test_the_windows_build_is_documented() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "build_windows.ps1" in readme
    assert "dist\\TiewTrade\\TiewTrade.exe" in readme
