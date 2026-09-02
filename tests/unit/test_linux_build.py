"""What a Linux build has to carry that the other two do not.

PyInstaller cannot cross-compile, and a Linux executable has to be linked
against a Linux Qt. The build therefore runs inside a container: `Dockerfile`
describes the Ubuntu that answers for the target machine, and
`packaging/build_linux.sh` runs the same checklist `make build` does before it
produces anything. These tests hold the three together.
"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPOSITORY_ROOT / "packaging" / "tiewtrade.spec"
DOCKERFILE_PATH = REPOSITORY_ROOT / "Dockerfile"
DOCKERIGNORE_PATH = REPOSITORY_ROOT / ".dockerignore"
CONTAINER_SCRIPT_PATH = REPOSITORY_ROOT / "packaging" / "build_linux.sh"
HOST_SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "build-linux.sh"
DOCUMENTATION_PATH = REPOSITORY_ROOT / "docs" / "linux-build.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_spec_names_the_keyring_backend_of_every_platform() -> None:
    """Linux keeps credentials in the Secret Service, reached over D-Bus.

    Left out, the backend is absent at runtime and every stored credential
    reads as "not set" without raising — LINE notifications would look
    switched off forever, exactly as they would on Windows without its own.
    """
    spec = _read(SPEC_PATH)

    assert '"keyring.backends.SecretService"' in spec
    assert '"keyring.backends.libsecret"' in spec


def test_the_spec_leaves_the_bundle_to_macos() -> None:
    """On Linux, as on Windows, the collected folder is the whole app."""
    spec = _read(SPEC_PATH)

    assert 'IS_MACOS = sys.platform == "darwin"' in spec
    assert "if IS_MACOS:" in spec


def test_the_container_script_runs_the_repository_checklist_before_building() -> None:
    """A build made from code that does not pass is a build nobody can trust."""
    script = _read(CONTAINER_SCRIPT_PATH)

    assert "set -euo pipefail" in script
    assert "-m pytest" in script
    assert "-m ruff check src tests" in script
    assert "-m ruff format --check src tests" in script
    assert "-m mypy" in script
    assert "-m tiewtrade.paper_replay_main" in script


def test_the_container_script_builds_the_collected_folder_from_the_spec() -> None:
    script = _read(CONTAINER_SCRIPT_PATH)

    assert "-m PyInstaller" in script
    assert "packaging/tiewtrade.spec" in script
    assert "dist/TiewTrade/TiewTrade" in script


def test_the_qt_widget_tests_are_excluded_only_where_they_cannot_run() -> None:
    """Qt's event loop segfaults under the x86_64 emulation an Apple Silicon
    Mac runs this container in, on 22.04 and 24.04 alike, while the same tests
    pass on macOS and on the native Linux runner in CI. A native x86_64 host
    has nothing to exclude, so the exclusion has to be liftable."""
    script = _read(CONTAINER_SCRIPT_PATH)

    assert "--ignore=tests/unit/ui" in script
    assert "TIEWTRADE_INCLUDE_UI_TESTS" in script


def test_the_container_runs_the_headless_qt_platform_for_its_checks() -> None:
    """A build container has no display; Qt aborts on import without one."""
    dockerfile = _read(DOCKERFILE_PATH)

    assert "QT_QPA_PLATFORM=offscreen" in dockerfile


def test_the_image_pins_the_ubuntu_that_answers_for_the_target_machine() -> None:
    """The oldest glibc the build links against is the oldest Ubuntu it runs
    on, so the image is pinned rather than left to follow `latest`."""
    dockerfile = _read(DOCKERFILE_PATH)

    assert "FROM ubuntu:22.04" in dockerfile
    assert "python3.12" in dockerfile


def test_the_host_script_builds_for_the_architecture_it_names() -> None:
    """An Apple Silicon Mac builds arm64 images by default, and an arm64
    executable does not start on an x86_64 Ubuntu."""
    script = _read(HOST_SCRIPT_PATH)

    assert "set -euo pipefail" in script
    assert "linux/amd64" in script
    assert "--platform" in script
    assert "dist-linux" in script


def test_the_build_context_leaves_out_what_must_not_reach_the_image() -> None:
    """A virtual environment built on macOS holds macOS binaries, and a
    trader's database is not part of any build."""
    ignored = _read(DOCKERIGNORE_PATH)

    assert ".venv" in ignored
    assert ".git" in ignored
    assert "__pycache__" in ignored
    assert "dist-linux" in ignored
    assert ".env" in ignored
    assert ".local" in ignored


def test_the_linux_build_is_documented() -> None:
    documentation = _read(DOCUMENTATION_PATH)
    readme = _read(REPOSITORY_ROOT / "README.md")

    assert "./scripts/build-linux.sh" in documentation
    assert "dist-linux/TiewTrade/TiewTrade" in documentation
    assert "libxcb-cursor0" in documentation
    assert "uname -m" in documentation
    assert "docs/linux-build.md" in readme
