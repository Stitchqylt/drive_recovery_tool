@echo off
:: Batch script to launch Roboflow-style Recovery Web Studio as Administrator
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Starting Recovery Web Studio...
    python dashboard.py
) else (
    echo ================================================================
    echo Requesting Administrator privileges (Required for PhysicalDrive raw I/O)...
    echo ================================================================
    powershell -Command "Start-Process python -ArgumentList 'dashboard.py' -Verb RunAs"
)
