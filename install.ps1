# Antigravity Raw Drive Recovery Studio - 1-Line Installer & Launcher
# Usage: irm https://raw.githubusercontent.com/Stitchqylt/drive_recovery_tool/main/install.ps1 | iex

Write-Host ""
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "    ANTIGRAVITY RAW DRIVE RECOVERY STUDIO (APPLESS INSTALLER)" -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host ""

$installDir = "$env:USERPROFILE\DriveRecoveryStudio"
$zipUrl = "https://raw.githubusercontent.com/Stitchqylt/drive_recovery_tool/main/DriveRecoveryTool_v1.0.zip"
$zipFile = "$env:TEMP\DriveRecoveryTool_v1.0.zip"

# Ensure Administrator Rights
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] Elevation required for direct physical disk access." -ForegroundColor Yellow
    Write-Host "[*] Relaunching in elevated administrator terminal..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"irm $zipUrl | iex`"" -Verb RunAs
    exit
}

Write-Host "[*] Downloading latest recovery package..." -ForegroundColor Green
Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile

if (Test-Path $installDir) {
    Remove-Item -Path $installDir -Recurse -Force
}

Write-Host "[*] Extracting to $installDir..." -ForegroundColor Green
Expand-Archive -Path $zipFile -DestinationPath $installDir -Force
Remove-Item -Path $zipFile -Force

Write-Host "[+] Launching Recovery Web Studio..." -ForegroundColor Cyan
Set-Location "$installDir\DriveRecoveryTool"
Start-Process python -ArgumentList "web_studio.py --open"
