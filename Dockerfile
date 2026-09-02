# The Ubuntu that builds the Linux executable.
#
# PyInstaller cannot cross-compile: a Linux build has to run on Linux, linked
# against a Linux Qt. This image is that Linux — nothing else. Source code is
# still written and run on macOS; the container only turns it into
# dist-linux/TiewTrade, and is thrown away afterwards.
#
# Build and run it through scripts/build-linux.sh, which pins the target
# architecture. See docs/linux-build.md.

# 22.04 rather than a newer LTS on purpose: a binary links against the glibc it
# was built on and runs on that version or newer, so building on the oldest
# Ubuntu still supported is what lets the result open on 22.04 and 24.04 alike.
# Pinned, not `latest`, so the same commit builds the same way next month.
FROM ubuntu:22.04

# 3.12 is what pyproject.toml, ruff and mypy target; 22.04 ships 3.10, so the
# interpreter comes from deadsnakes below.

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The build container has no display. Qt aborts on import without a
    # platform plugin, which would stop the test run and PyInstaller's own
    # analysis before either reached the code being built.
    QT_QPA_PLATFORM=offscreen

# Two sets of packages: what installs Python, and what Qt needs to load at all.
# PySide6's wheel carries the Qt libraries themselves but not their system
# dependencies — the xcb libraries below are what the platform plugin links
# against, and PyInstaller collects that plugin only if it can load it here.
RUN apt-get update \
 && apt-get install --no-install-recommends --yes \
      ca-certificates \
      curl \
      git \
      gnupg \
      software-properties-common \
 && add-apt-repository --yes ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install --no-install-recommends --yes \
      python3.12 \
      python3.12-dev \
      python3.12-venv \
      binutils \
      libdbus-1-3 \
      libegl1 \
      libfontconfig1 \
      libfreetype6 \
      libgl1 \
      libglib2.0-0 \
      libxcb-cursor0 \
      libxcb-icccm4 \
      libxcb-image0 \
      libxcb-keysyms1 \
      libxcb-randr0 \
      libxcb-render-util0 \
      libxcb-shape0 \
      libxcb-shm0 \
      libxcb-sync1 \
      libxcb-xfixes0 \
      libxcb-xinerama0 \
      libxcb1 \
      libxkbcommon-x11-0 \
      libxkbcommon0 \
 && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python3.12 -m venv "${VIRTUAL_ENV}" \
 && python -m pip install --upgrade pip

# Dependencies before source, so editing code does not reinstall PySide6.
# pyproject.toml is the one list of them this repository has; reading it here
# keeps the image from holding a second copy that could drift.
COPY pyproject.toml ./
RUN python - <<'PYTHON' > /tmp/requirements.txt \
 && python -m pip install --requirement /tmp/requirements.txt
import tomllib

with open("pyproject.toml", "rb") as pyproject:
    project = tomllib.load(pyproject)["project"]

for requirement in [
    *project["dependencies"],
    *project["optional-dependencies"]["dev"],
]:
    print(requirement)
PYTHON

WORKDIR /app

# The whole checkout, minus everything .dockerignore holds back. The build
# needs tests/ as much as src/: it runs the repository's checks before it
# produces anything.
COPY . .

# `docker run` with no arguments builds. Arguments are passed to the script —
# --skip-verify is the only one it takes.
ENTRYPOINT ["packaging/build_linux.sh"]
