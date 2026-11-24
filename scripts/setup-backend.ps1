<#
.SYNOPSIS
  Install the PaperOrchestra Python backend for PaperOrchestra Studio (Windows).

.DESCRIPTION
  1. Finds Python 3.11+ (py launcher, python, python3).
  2. Copies the "paper-orchestra" folder next to this script to
     %USERPROFILE%\paper-orchestra (Studio looks there by default). An existing
     install is updated in place; its .venv, .env and providers.json are kept.
  3. Creates paper-orchestra\.venv and installs requirements.txt.
  4. Checks for a TeX distribution (latexmk, pdflatex, xelatex).

  Run it from the unzipped backend package (or from a clone of the repo):
    powershell -ExecutionPolicy Bypass -File .\setup-backend.ps1
    powershell -ExecutionPolicy Bypass -File .\setup-backend.ps1 -Target D:\paper-orchestra
    powershell -ExecutionPolicy Bypass -File .\setup-backend.ps1 -InPlace

.PARAMETER Target
  Install folder. Default: %USERPROFILE%\paper-orchestra.

.PARAMETER InPlace
  Use the "paper-orchestra" folder where it is, without copying it.

.PARAMETER Python
  Python interpreter to build the virtual environment with (3.11 or newer).
#>
[CmdletBinding()]
param(
  [string]$Target = "",
  [switch]$InPlace,
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
# This file is ASCII-only on purpose: Windows PowerShell 5.1 reads scripts
# without a BOM in the ANSI code page.

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    [ok] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "    [!]  $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host ""; Write-Host "Error: $Text" -ForegroundColor Red; exit 1 }

Write-Host "=== PaperOrchestra backend setup ===" -ForegroundColor Cyan

# ---------------------------------------------------------------- source folder
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = $null
foreach ($c in @((Join-Path $ScriptDir "paper-orchestra"), (Join-Path (Split-Path -Parent $ScriptDir) "paper-orchestra"))) {
  if (Test-Path (Join-Path $c "paper_writing_cli.py")) { $Source = (Resolve-Path $c).Path; break }
}
if (-not $Source) { Fail "could not find the paper-orchestra folder next to this script." }
Write-Ok "Backend source: $Source"

if ($InPlace) {
  $Dest = $Source
} else {
  if (-not $Target) { $Target = Join-Path $env:USERPROFILE "paper-orchestra" }
  $Dest = [System.IO.Path]::GetFullPath($Target)
}

# Some packages (anthropic) ship files ~150 characters deep inside .venv, so a
# long install folder hits the 260-character Windows path limit during pip.
$longPaths = $false
try { $longPaths = ((Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled -eq 1) } catch { }
if (-not $longPaths -and $Dest.Length -gt 95) {
  Fail ("the install folder path is too long for Windows ($($Dest.Length) characters, limit 95):`n  $Dest`n" +
    "  Use a shorter one, e.g.  -Target C:\paper-orchestra`n" +
    "  or enable long paths: https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation")
}

# ---------------------------------------------------------------- python
Write-Step "Looking for Python 3.11 or newer"

function Test-Python([string[]]$Cmd) {
  try {
    $exe = $Cmd[0]
    $rest = @()
    if ($Cmd.Count -gt 1) { $rest = $Cmd[1..($Cmd.Count - 1)] }
    $out = & $exe @rest -c "import sys; print('OK' if sys.version_info >= (3, 11) else 'OLD', sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out -match "^OK (.+)$") { return $Matches[1].Trim() }
  } catch { }
  return $null
}

$PyExe = $null
if ($Python) {
  $PyExe = Test-Python @($Python)
  if (-not $PyExe) { Fail "$Python is not Python 3.11 or newer." }
} else {
  $tries = @()
  if (Get-Command "py" -ErrorAction SilentlyContinue) {
    $tries += ,@("py", "-3.13"); $tries += ,@("py", "-3.12"); $tries += ,@("py", "-3.11"); $tries += ,@("py", "-3")
  }
  $tries += ,@("python"); $tries += ,@("python3")
  foreach ($t in $tries) {
    $PyExe = Test-Python $t
    if ($PyExe) { break }
  }
}
if (-not $PyExe) {
  Fail ("Python 3.11 or newer was not found.`n" +
    "  Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),`n" +
    "  or run:  winget install Python.Python.3.12`n" +
    "  then run this script again.")
}
Write-Ok "Python: $PyExe"

# ---------------------------------------------------------------- copy
if ($Dest -ne $Source) {
  Write-Step "Copying the backend to $Dest"
  New-Item -ItemType Directory -Force -Path $Dest | Out-Null
  # Keep the user's environment, secrets and provider overrides.
  & robocopy $Source $Dest /E /NFL /NDL /NJH /NJS /NP /XD ".venv" "__pycache__" /XF ".env" "providers.json" | Out-Null
  if ($LASTEXITCODE -ge 8) { Fail "copying files failed (robocopy exit code $LASTEXITCODE)." }
  $global:LASTEXITCODE = 0
  Write-Ok "Files copied"
}

# ---------------------------------------------------------------- venv
$VenvPy = Join-Path $Dest ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
  Write-Step "Reusing the existing virtual environment"
} else {
  Write-Step "Creating the virtual environment (.venv)"
  & $PyExe -m venv (Join-Path $Dest ".venv")
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Fail "creating the virtual environment failed." }
}
Write-Ok $VenvPy

Write-Step "Installing Python packages (this can take a few minutes)"
& $VenvPy -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail "upgrading pip failed." }
& $VenvPy -m pip install --disable-pip-version-check --prefer-binary -r (Join-Path $Dest "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "installing requirements.txt failed (see the pip output above)." }
Write-Ok "Packages installed"

& $VenvPy (Join-Path $Dest "paper_writing_cli.py") --help *> $null
if ($LASTEXITCODE -eq 0) { Write-Ok "paper_writing_cli.py starts" } else { Write-Warn2 "paper_writing_cli.py --help exited with code $LASTEXITCODE" }

# ---------------------------------------------------------------- TeX
Write-Step "Checking TeX (needed to compile PDFs)"
$texMissing = @()
foreach ($tool in @("latexmk", "pdflatex", "xelatex", "bibtex")) {
  $cmd = Get-Command $tool -ErrorAction SilentlyContinue
  if ($cmd) { Write-Ok "$tool  $($cmd.Source)" } else { $texMissing += $tool }
}
if ($texMissing.Count -gt 0) {
  Write-Warn2 ("Not found: " + ($texMissing -join ", "))
  Write-Warn2 "Install MiKTeX (winget install MiKTeX.MiKTeX) or TeX Live (https://tug.org/texlive/)."
  Write-Warn2 "MiKTeX's latexmk also needs Perl (winget install StrawberryPerl.StrawberryPerl)."
  Write-Warn2 "Studio still runs without TeX, but cannot build PDFs."
}

# ---------------------------------------------------------------- done
$defaultDest = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "paper-orchestra"))
Write-Host ""
Write-Host "Backend ready." -ForegroundColor Green
Write-Host "  Backend folder: $Dest"
Write-Host "  Python:         $VenvPy"
Write-Host ""
if ($Dest -eq $defaultDest) {
  Write-Host "Open PaperOrchestra Studio. On first start it finds this backend by itself."
  Write-Host "If Studio was opened before, set both paths above in Studio > Environment."
} else {
  Write-Host "In PaperOrchestra Studio > Environment, set the two paths above."
}
Write-Host "Add your model API keys in Studio > Environment as well."
