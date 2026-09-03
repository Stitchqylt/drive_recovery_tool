@echo off
:: Batch script to launch GUI with elevated Administrator rights
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Starting NTFS Raw Drive Recovery GUI...
    python main.py
) else (
    echo ================================================================
    echo Requesting Administrator privileges (Required for PhysicalDrive raw I/O)...
    echo ================================================================
    powershell -Command "Start-Process python -ArgumentList 'main.py' -Verb RunAs"
)
