<# 
.SYNOPSIS
    drive-recovery-tool installer for Windows

.DESCRIPTION
    Installs drive-recovery-tool to %LOCALAPPDATA%\drive-recovery-tool
    and adds a 'drive-recovery' command to PATH.

.USAGE
    irm https://raw.githubusercontent.com/Stitchqylt/drive_recovery_tool/main/install.ps1 | iex
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO = "Stitchqylt/drive_recovery_tool"
$BRANCH = "main"
$INSTALL_DIR = "$env:LOCALAPPDATA\drive-recovery-tool"
$BIN_DIR = "$env:USERPROFILE\.local\bin"
$BIN_NAME = "drive-recovery.exe"

Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     drive-recovery-tool installer (Windows)                  ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check Python
try {
    $pyVersion = python --version 2>&1
    Write-Host "✓ $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ Python not found. Install from python.org or: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}

# Check admin (warn only)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "⚠ Not running as Administrator. Physical drive access will require 'Run as Administrator' later." -ForegroundColor Yellow
}

# Create directories
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $BIN_DIR | Out-Null

# Clone/update repo
Write-Host "→ Installing to $INSTALL_DIR"
if (Test-Path "$INSTALL_DIR\.git") {
    Set-Location $INSTALL_DIR
    git fetch origin $BRANCH
    git reset --hard "origin/$BRANCH"
    Write-Host "✓ Updated existing installation" -ForegroundColor Green
} else {
    git clone --depth 1 --branch $BRANCH "https://github.com/$REPO.git" $INSTALL_DIR
    Write-Host "✓ Cloned repository" -ForegroundColor Green
}

# Create launcher batch file
$launcherPath = "$BIN_DIR\$BIN_NAME"
@"
@echo off
cd /d "$INSTALL_DIR"
python main.py %*
"@ | Set-Content -Encoding ASCII $launcherPath

# Create PowerShell wrapper too
$psLauncher = "$BIN_DIR\drive-recovery.ps1"
@"
# drive-recovery-tool PowerShell wrapper
Set-Location "$INSTALL_DIR"
python main.py `@Args
"@ | Set-Content $psLauncher

# Add to PATH (user scope)
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*$BIN_DIR*") {
    [Environment]::SetEnvironmentVariable("PATH", "$currentPath;$BIN_DIR", "User")
    Write-Host "✓ Added $BIN_DIR to user PATH" -ForegroundColor Green
    $needsRefresh = $true
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Installation complete!                                       ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

if ($needsRefresh) {
    Write-Host "⚠ Restart your terminal (or run: refreshenv) to use 'drive-recovery' command" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "Usage:" -ForegroundColor Cyan
Write-Host "  drive-recovery --web          # Web dashboard (Run as Admin for physical drives)"
Write-Host "  drive-recovery --cli          # Command line interface"
Write-Host "  drive-recovery --help         # Show all options"
Write-Host ""
Write-Host "Examples:" -ForegroundColor Cyan
Write-Host "  # Web UI (right-click terminal -> Run as Administrator)"
Write-Host "  drive-recovery --web"
Write-Host ""
Write-Host "  # Recover physical drive (Run as Administrator)"
Write-Host "  drive-recovery --cli --drive 1"
Write-Host ""
Write-Host "  # Recover disk image"
Write-Host "  drive-recovery --cli --drive disk.img"