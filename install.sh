#!/bin/bash
# drive-recovery-tool installer for macOS/Linux
# Usage: curl -sSL https://raw.githubusercontent.com/Stitchqylt/drive_recovery_tool/main/install.sh | bash

set -e

REPO="Stitchqylt/drive_recovery_tool"
BRANCH="main"
INSTALL_DIR="${HOME}/.local/share/drive-recovery-tool"
BIN_DIR="${HOME}/.local/bin"
BIN_NAME="drive-recovery"

echo "================================================================"
echo "|     drive-recovery-tool installer (macOS/Linux)              |"
echo "================================================================"
echo ""

# Check Python 3.8+
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
REQUIRED_VERSION="3.8"
if ! python3 -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)"; then
    echo "FAIL Python 3.8+ required (found $PYTHON_VERSION)"
    exit 1
fi
echo "OK Python $PYTHON_VERSION"

# Create directories
mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# Clone/update repo
echo "-> Installing to $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH"
    echo "OK Updated existing installation"
else
    git clone --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" "$INSTALL_DIR"
    echo "OK Cloned repository"
fi

# Create launcher script
cat > "$BIN_DIR/$BIN_NAME" << 'LAUNCHER_EOF'
#!/bin/bash
# drive-recovery-tool launcher
INSTALL_DIR="${HOME}/.local/share/drive-recovery-tool"
cd "$INSTALL_DIR"
exec python3 main.py "$@"
LAUNCHER_EOF

chmod +x "$BIN_DIR/$BIN_NAME"

# Check PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "WARN Add $BIN_DIR to your PATH:"
    echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
    echo "    source ~/.zshrc"
    echo ""
    echo "Or run directly: $BIN_DIR/$BIN_NAME --web"
fi

echo ""
echo "================================================================"
echo "|  Installation complete!                                       |"
echo "================================================================"
echo ""
echo "Usage:"
echo "  $BIN_NAME --web          # Web dashboard (sudo for physical drives)"
echo "  $BIN_NAME --cli          # Command line interface"
echo "  $BIN_NAME --help         # Show all options"
echo ""
echo "Examples:"
echo "  sudo $BIN_NAME --web                    # Web UI"
echo "  sudo $BIN_NAME --cli --drive /dev/rdisk2  # Recover physical drive"
echo "  $BIN_NAME --cli --drive disk.img          # Recover disk image"