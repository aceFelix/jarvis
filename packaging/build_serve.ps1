# Freeze the jarvis serve backend into a standalone executable (PyInstaller onedir).
#
# Purpose: before the desktop shell (jarvis-desktop) builds its NSIS installer, run
# this to produce dist/jarvis-serve/, which electron-builder copies into the package
# via extraResources. Can also be run standalone for backend self-testing.
#
# Prereqs: jarvis .venv installed with ALL extras for a full-capability package:
#   pip install -e ".[all]"
# (gui/browser/camera/vision/voice/mcp/daemon/wechat). PyInstaller itself is a
# build-machine tool only, never a runtime dependency.
#
# Usage (from anywhere in the jarvis repo):
#   powershell -ExecutionPolicy Bypass -File packaging\build_serve.ps1
#
# 2026-11-07: the package now bundles the playwright browser kernels too, so the
# desktop installer works offline for browser automation. Both the headless shell
# and the full chromium are downloaded into dist/jarvis-serve/ms-playwright and
# serve_entry points PLAYWRIGHT_BROWSERS_PATH there when frozen.
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

# Bundle playwright browser kernels next to the frozen backend (offline use).
$Browsers = Join-Path $Root "dist\jarvis-serve\ms-playwright"
$env:PLAYWRIGHT_BROWSERS_PATH = $Browsers
Write-Host "Installing playwright browsers into $Browsers"
& $Py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright browser install failed (exit code $LASTEXITCODE)" }

$Headless = Get-ChildItem -Path $Browsers -Directory -Filter "chromium_headless_shell-*" -ErrorAction SilentlyContinue
$Full = Get-ChildItem -Path $Browsers -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike "chromium_headless_shell-*" }
if (-not $Headless) { throw "headless shell kernel missing under $Browsers" }
if (-not $Full) { throw "full chromium kernel missing under $Browsers" }

# Frozen capability gate: fail the build when a packaged ability is missing.
# This is the guard against "works in dev venv, missing in packaged exe".
Write-Host "Running frozen capability gate: $Exe --caps"
& $Exe --caps
if ($LASTEXITCODE -ne 0) { throw "frozen capability gate failed (exit code $LASTEXITCODE)" }

Write-Host "Freeze complete: $Exe (browsers: $Browsers)"
