#!/usr/bin/env bash
# Antigravity Raw Drive Recovery Studio - 1-Line macOS & Linux Installer / Launcher
# Usage: curl -sSL https://raw.githubusercontent.com/Stitchqylt/drive_recovery_tool/main/install.sh | bash

set -e

echo ""
echo -e "\033[1;36m========================================================================\033[0m"
echo -e "\033[1;36m    ANTIGRAVITY RAW DRIVE RECOVERY STUDIO (macOS / Linux Launcher)\033[0m"
echo -e "\033[1;36m========================================================================\033[0m"
echo ""

INSTALL_DIR="$HOME/DriveRecoveryStudio"
ZIP_URL="https://raw.githubusercontent.com/Stitchqylt/drive_recovery_tool/main/DriveRecoveryTool_v1.0.zip"
ZIP_FILE="/tmp/DriveRecoveryTool_v1.0.zip"

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "\033[1;31m[!] Python 3 is required. Please install Python 3 or Xcode Command Line Tools.\033[0m"
    exit 1
fi

echo -e "\033[1;32m[*] Downloading latest recovery package...\033[0m"
curl -sSL "$ZIP_URL" -o "$ZIP_FILE"

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

echo -e "\033[1;32m[*] Extracting to $INSTALL_DIR...\033[0m"
unzip -q -o "$ZIP_FILE" -d "$INSTALL_DIR"
rm -f "$ZIP_FILE"

echo -e "\033[1;36m[+] Launching Recovery Web Studio locally...\033[0m"
cd "$INSTALL_DIR/DriveRecoveryTool"

# Open browser on macOS
if [[ "$OSTYPE" == "darwin"* ]]; then
    (sleep 1 && open "http://127.0.0.1:8080") &
fi

python3 web_studio.py
