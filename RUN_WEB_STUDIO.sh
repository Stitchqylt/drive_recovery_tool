#!/bin/bash
# Drive Rescue Recovery Web Studio Launcher (macOS/Linux)
# Run this script to start the web-based recovery dashboard

echo "========================================================================"
echo "  Drive Rescue Dashboard (WEB DASHBOARD) - macOS/Linux"
echo "========================================================================"
echo ""

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "[X] python3 not found. Please install Python 3.8+"
    exit 1
fi

# Check if we're in the right directory
if [ ! -f "main.py" ]; then
    echo "[X] main.py not found. Please run this script from the drive_recovery_tool directory."
    exit 1
fi

echo "[*] Starting Web Studio on http://127.0.0.1:8080"
echo "[*] Press Ctrl+C to stop"
echo ""

# Run with --web flag
python3 main.py --web