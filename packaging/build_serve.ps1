# Freeze the jarvis serve backend into a standalone executable (PyInstaller onedir).
#
# Purpose: before the desktop shell (jarvis-desktop) builds its NSIS installer, run
# this to produce dist/jarvis-serve/, which electron-builder copies into the package
# via extraResources. Can also be run standalone for backend self-testing.
#
# Prereqs: jarvis .venv installed as `pip install -e .` (websockets is a core dep),
# and the build tool PyInstaller available (`pip install pyinstaller`; build-machine
# only, not a runtime dependency).
#
# Usage (from anywhere in the jarvis repo):
#   powershell -ExecutionPolicy Bypass -File packaging\build_serve.ps1
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less UTF-8 as the
# system codepage (GBK), which corrupts non-ASCII string literals and breaks parsing.
#
# @author aceFelix
$ErrorActionPreference = "Stop"

# Script dir (packaging) -> repo root (jarvis).
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Prefer the project venv Python, fall back to PATH python.
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    $Py = $VenvPy
} else {
    $Py = "python"
}

# Ensure PyInstaller exists (build-time tool; install on demand if missing).
& $Py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not installed, installing..."
    & $Py -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller install failed" }
}

Write-Host "Freezing backend: $Py -m PyInstaller packaging\jarvis-serve.spec"
& $Py -m PyInstaller --noconfirm --clean `
    --distpath dist `
    --workpath build\pyi `
    packaging\jarvis-serve.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit code $LASTEXITCODE)" }

$Exe = Join-Path $Root "dist\jarvis-serve\jarvis-serve.exe"
if (-not (Test-Path $Exe)) { throw "Artifact not found: $Exe" }
Write-Host "Freeze complete: $Exe"
