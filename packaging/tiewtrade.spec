# PyInstaller build for the TiewTrade desktop app (macOS .app bundle).
#
# Nothing here bundles a database. The app writes to
# ~/Library/Application Support/TiewTrade at runtime and builds its own
# schema on first launch, so the only thing that has to reach the bundle is
# code — plus the one import Python cannot see statically (see keyring below).

import os
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).parent  # noqa: F821 — PyInstaller injects SPECPATH
SOURCE_ROOT = PROJECT_ROOT / "src"

with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
    VERSION = tomllib.load(pyproject)["project"]["version"]

# keyring finds its backends through entry points, which PyInstaller cannot
# follow. Left out, the macOS Keychain backend is simply absent at runtime and
# the app reads every stored credential as "not set" without raising — LINE
# notifications would look switched off forever. Name it explicitly.
# Signing is opt-in through the environment so an unsigned local build stays
# one command. Without an identity PyInstaller ad-hoc signs the bundle, which
# runs on this machine and nowhere else. `make sign-build` sets both.
SIGN_IDENTITY = os.environ.get("TIEWTRADE_SIGN_IDENTITY") or None
ENTITLEMENTS = (
    str(PROJECT_ROOT / "packaging" / "entitlements.plist") if SIGN_IDENTITY else None
)

HIDDEN_IMPORTS = [
    "keyring.backends.macOS",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.null",
]

# The UI imports QtCore, QtGui and QtWidgets and nothing else. The PySide6 hook
# would otherwise collect every Qt module installed, most of the bundle's size.
EXCLUDED_MODULES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    # Development-only dependencies that must never reach a trader's machine.
    "mypy",
    "pytest",
    "ruff",
    "tkinter",
]

analysis = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "packaging" / "entry_point.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=0,
)

archive = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TiewTrade",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A GUI app has no terminal to print to. Everything the app has to say goes
    # to the log file it writes beside the database.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=SIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
)

collection = COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="TiewTrade",
)

bundle = BUNDLE(  # noqa: F821
    collection,
    name="TiewTrade.app",
    icon=None,
    bundle_identifier="com.tiewtrade.desktop",
    version=VERSION,
    info_plist={
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        # Internal Alpha, Paper-first: the app talks to Binance for market data
        # only, and holds no document types of its own.
        "LSApplicationCategoryType": "public.app-category.finance",
        "LSMinimumSystemVersion": "12.0",
    },
)
