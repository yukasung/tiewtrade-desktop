# Builds dist\TiewTrade\TiewTrade.exe on a Windows machine.
#
# PyInstaller cannot cross-compile: the macOS `make build` produces a macOS
# bundle and nothing else, so the Windows executable has to be built here,
# from the same packaging\tiewtrade.spec. Everything the app needs at runtime
# is collected beside the .exe — nothing is installed on the machine that
# receives it, and no database is bundled. The app makes its own under
# %LOCALAPPDATA%\TiewTrade on first launch.
#
# Setup, once, from the repository root with Python 3.12 on PATH:
#
#   py -3.12 -m venv .venv
#   .venv\Scripts\python.exe -m pip install --upgrade pip
#   .venv\Scripts\python.exe -m pip install -e ".[dev]"
#
# Then:
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Pass -SkipVerify only when the checks were already run in this checkout.

[CmdletBinding()]
param(
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"

# The repository root, whichever directory the script was started from.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "$Python is missing - create the virtual environment first (see the comment at the top of this script)."
}

# PowerShell does not stop on a failed native command, so every exit code has
# to be read. A build made from code that did not pass is a build nobody can
# trust, and it would look exactly like one that did.
function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "==> $Name"
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

if (-not $SkipVerify) {
    $env:PYTHONPATH = "src"
    Invoke-Step -Name "Tests" -Arguments @("-m", "pytest", "-q")
    Invoke-Step -Name "Ruff lint" -Arguments @("-m", "ruff", "check", "src", "tests")
    Invoke-Step -Name "Ruff format" -Arguments @(
        "-m", "ruff", "format", "--check", "src", "tests"
    )
    Invoke-Step -Name "Mypy" -Arguments @("-m", "mypy")
    Invoke-Step -Name "Deterministic replay" -Arguments @(
        "-m", "tiewtrade.paper_replay_main",
        "tests\fixtures\btcusdt_5m_tracer.csv",
        "--capital-per-entry", "250",
        "--max-entries", "4"
    )

    Write-Host "==> Committed whitespace"
    git diff --check
    if ($LASTEXITCODE -ne 0) {
        throw "git diff --check failed with exit code $LASTEXITCODE."
    }
}

# A stale folder from an earlier build would otherwise ship files the current
# code no longer collects.
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist

# The spec points PyInstaller at src\, so the frozen app is built from this
# checkout rather than from whatever is installed.
Invoke-Step -Name "PyInstaller" -Arguments @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--distpath", "dist",
    "--workpath", "build",
    "packaging\tiewtrade.spec"
)

$Executable = "dist\TiewTrade\TiewTrade.exe"
if (-not (Test-Path $Executable)) {
    throw "The build finished but $Executable is missing."
}

Write-Host ""
Write-Host "Built $Executable"
Write-Host "Ship the whole dist\TiewTrade folder - the .exe does not run without it."
