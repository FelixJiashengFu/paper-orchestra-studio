<#
.SYNOPSIS
  Build PaperOrchestra Studio for Windows (.exe + NSIS setup.exe + MSI).

.DESCRIPTION
  Checks the toolchain (Node.js, npm, Rust/cargo, MSVC C++ build tools,
  WebView2), then runs: npm install -> npm run build -> (cargo test) ->
  npx tauri build, and prints where the installers landed.

  Run from any folder:
    powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1 -SkipTests
    powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1 -Bundles nsis

.PARAMETER SkipTests
  Skip "cargo test" before bundling.

.PARAMETER SkipInstall
  Skip "npm install" (use the node_modules already present).

.PARAMETER Bundles
  Comma-separated Tauri bundle targets (e.g. "nsis" or "nsis,msi").
  Default: the "targets" from src-tauri/tauri.conf.json (all = NSIS + MSI).
  Building the MSI needs WiX, which the Tauri CLI downloads on first use.
#>
[CmdletBinding()]
param(
  [switch]$SkipTests,
  [switch]$SkipInstall,
  [string]$Bundles = ""
)

$ErrorActionPreference = "Stop"
# This file is ASCII-only on purpose: Windows PowerShell 5.1 reads scripts
# without a BOM in the ANSI code page.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    [ok] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "    [!]  $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host ""; Write-Host "Error: $Text" -ForegroundColor Red; exit 1 }

function Invoke-Native([string]$What, [scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) { Fail "$What failed (exit code $LASTEXITCODE)." }
}

function Get-ToolVersion([string]$Name, [string[]]$VersionArgs = @("--version")) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) { return $null }
  try { return ((& $Name @VersionArgs 2>$null) | Select-Object -First 1) } catch { return "found" }
}

Write-Host "=== PaperOrchestra Studio Windows Builder ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectDir"

# ---------------------------------------------------------------- prerequisites
Write-Step "Checking prerequisites"

$missing = @()
$node = Get-ToolVersion "node"
if ($node) { Write-Ok "Node.js $node" } else { $missing += "Node.js 18+ (https://nodejs.org/)" }
$npm = Get-ToolVersion "npm"
if ($npm) { Write-Ok "npm $npm" } else { $missing += "npm (installed with Node.js)" }
$cargo = Get-ToolVersion "cargo"
if ($cargo) { Write-Ok "$cargo" } else { $missing += "Rust toolchain (https://rustup.rs/, choose the MSVC host)" }
$rustc = Get-ToolVersion "rustc"
if ($rustc) { Write-Ok "$rustc" }

if ($missing.Count -gt 0) {
  Fail ("missing tools:`n  - " + ($missing -join "`n  - "))
}

if ($node -match "v(\d+)\.") {
  if ([int]$Matches[1] -lt 18) { Fail "Node.js 18 or newer is required (found $node)." }
}

# MSVC C++ build tools (Rust's MSVC target links with link.exe)
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$hasMsvc = $false
if (Test-Path $vswhere) {
  $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
  if ($vsPath) { $hasMsvc = $true; Write-Ok "MSVC build tools: $vsPath" }
}
if (-not $hasMsvc) {
  if (Get-Command "link.exe" -ErrorAction SilentlyContinue) {
    $hasMsvc = $true; Write-Ok "link.exe found on PATH"
  } else {
    Write-Warn2 "Visual Studio C++ build tools were not detected."
    Write-Warn2 "Install 'Desktop development with C++' from https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    Write-Warn2 "if the Rust build fails with a linker error."
  }
}

# WebView2 runtime (needed to RUN the app; preinstalled on Windows 11 and current Windows 10)
$wv2Keys = @(
  "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
  "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
  "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
$wv2 = $null
foreach ($k in $wv2Keys) {
  try {
    $v = (Get-ItemProperty -Path $k -Name pv -ErrorAction Stop).pv
    if ($v -and $v -ne "0.0.0.0") { $wv2 = $v; break }
  } catch { }
}
if ($wv2) {
  Write-Ok "WebView2 runtime $wv2"
} else {
  Write-Warn2 "WebView2 runtime not detected on this machine. The build still works; the"
  Write-Warn2 "installer downloads WebView2 on target machines that lack it. To run the app"
  Write-Warn2 "here, install it from https://developer.microsoft.com/microsoft-edge/webview2/"
}

# ---------------------------------------------------------------- build
if (-not $SkipInstall) {
  Write-Step "Installing npm dependencies (npm install)"
  Invoke-Native "npm install" { npm install }
}

Write-Step "Building the frontend (npm run build)"
Invoke-Native "npm run build" { npm run build }

if (-not $SkipTests) {
  Write-Step "Running Rust unit tests (cargo test)"
  Push-Location (Join-Path $ProjectDir "src-tauri")
  try { Invoke-Native "cargo test" { cargo test } } finally { Pop-Location }
}

Write-Step "Building the Windows bundles (npx tauri build)"
$tauriArgs = @("tauri", "build")
if ($Bundles.Trim()) { $tauriArgs += @("--bundles", $Bundles.Trim()) }
Invoke-Native "tauri build" { npx @tauriArgs }

# ---------------------------------------------------------------- report
$releaseDir = Join-Path $ProjectDir "src-tauri\target\release"
$bundleDir = Join-Path $releaseDir "bundle"

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green

$exe = Get-ChildItem -Path $releaseDir -Filter "*.exe" -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notlike "build-script*" } | Select-Object -First 1
if ($exe) { Write-Host "  App (portable .exe): $($exe.FullName)" }

$setup = Get-ChildItem -Path (Join-Path $bundleDir "nsis") -Filter "*-setup.exe" -File -ErrorAction SilentlyContinue
foreach ($f in $setup) { Write-Host "  NSIS installer:      $($f.FullName)" }

$msi = Get-ChildItem -Path (Join-Path $bundleDir "msi") -Filter "*.msi" -File -ErrorAction SilentlyContinue
foreach ($f in $msi) { Write-Host "  MSI installer:       $($f.FullName)" }

if (-not $setup -and -not $msi) {
  Write-Warn2 "No installer found under $bundleDir (check the tauri build output above)."
}
