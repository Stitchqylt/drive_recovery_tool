@echo off
:: Batch script to launch CLI with elevated Administrator rights
net session >nul 2>&1
if %errorLevel% == 0 (
    python main.py --cli %*
) else (
    echo ================================================================
    echo Requesting Administrator privileges (Required for PhysicalDrive raw I/O)...
    echo ================================================================
    powershell -Command "Start-Process cmd -ArgumentList '/k python %~dp0main.py --cli' -Verb RunAs"
)
