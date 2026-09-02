#!/usr/bin/env bash
# Builds dist/TiewTrade/TiewTrade inside the Ubuntu container.
#
# This is the Linux counterpart of packaging/build_windows.ps1 and of the
# `build` target in the Makefile: the same repository checklist first, then
# PyInstaller against the same packaging/tiewtrade.spec. It is not meant to be
# run on macOS — PyInstaller cannot cross-compile, and the checks here expect
# the container's interpreter on PATH. Run scripts/build-linux.sh instead.
#
# Pass --skip-verify only when the checks were already run in this checkout.

set -euo pipefail

cd "$(dirname "$0")/.."

# The spec points PyInstaller at src/, and every check below imports from
# there, so the build is made from this checkout rather than from anything
# installed.
export PYTHONPATH=src

# The container has no display; Qt aborts on import without a platform plugin.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

# Where the collected folder lands. The default is the directory
# scripts/build-linux.sh mounts dist-linux/ onto.
DISTRIBUTION_PATH="${TIEWTRADE_DIST_PATH:-dist}"

SKIP_VERIFY=0
for argument in "$@"; do
  case "$argument" in
    --skip-verify) SKIP_VERIFY=1 ;;
    *)
      echo "Unknown argument: $argument" >&2
      exit 2
      ;;
  esac
done

step() {
  echo "==> $1"
  shift
  "$@"
}

# Qt's widget tests cannot run here. On an Apple Silicon Mac this container is
# an emulated x86_64, and Qt's event loop segfaults under that emulation —
# reproducibly, on Ubuntu 22.04 and 24.04 alike, while the same tests pass on
# macOS (`make verify`) and on the native Linux runner in CI. Excluding them is
# a statement about the emulator, not about the code. On a real x86_64 Linux
# host there is no emulation and nothing to exclude:
#
#   TIEWTRADE_INCLUDE_UI_TESTS=1 packaging/build_linux.sh
#
TEST_ARGUMENTS=(-q)
if [ "${TIEWTRADE_INCLUDE_UI_TESTS:-0}" != "1" ]; then
  TEST_ARGUMENTS+=(--ignore=tests/unit/ui)
fi

if [ "$SKIP_VERIFY" -eq 0 ]; then
  step "Tests" python -m pytest "${TEST_ARGUMENTS[@]}"
  step "Ruff lint" python -m ruff check src tests
  step "Ruff format" python -m ruff format --check src tests
  step "Mypy" python -m mypy
  step "Deterministic replay" python -m tiewtrade.paper_replay_main \
    tests/fixtures/btcusdt_5m_tracer.csv \
    --capital-per-entry 250 \
    --max-entries 4
fi

# A stale folder from an earlier build would otherwise ship files the current
# code no longer collects. Only this build's own output is removed — the mount
# holds nothing else.
rm -rf "${DISTRIBUTION_PATH:?}/TiewTrade" build

step "PyInstaller" python -m PyInstaller \
  --noconfirm \
  --distpath "$DISTRIBUTION_PATH" \
  --workpath build \
  packaging/tiewtrade.spec

EXECUTABLE="$DISTRIBUTION_PATH/TiewTrade/TiewTrade"
if [ ! -x "$EXECUTABLE" ]; then
  echo "The build finished but $EXECUTABLE is missing." >&2
  exit 1
fi

echo
echo "Built $EXECUTABLE"
# The architecture is the thing most likely to be wrong and least likely to
# be noticed until the executable reaches a machine that cannot run it.
readelf --file-header "$EXECUTABLE" | grep -E "Class|Machine"
echo "Ship the whole TiewTrade folder — the executable does not run without it."

if [ "$SKIP_VERIFY" -eq 0 ] && [ "${TIEWTRADE_INCLUDE_UI_TESTS:-0}" != "1" ]; then
  echo "Qt widget tests were not run here — see the comment in this script."
fi
