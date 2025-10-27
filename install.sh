#!/bin/bash

# CLI Recorder Installation Script
# Installs clirec with all dependencies

set -e

echo "🚀 CLI Recorder Installer"
echo "========================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check OS
OS=$(uname -s)
echo "Detected OS: $OS"

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Install dependencies based on OS
install_dependencies() {
    echo -e "${YELLOW}Installing dependencies...${NC}"
    
    if [[ "$OS" == "Linux" ]]; then
        # Check package manager
        if command_exists apt-get; then
            echo "Using apt-get..."
            sudo apt-get update
            sudo apt-get install -y python3 python3-pip python3-tk
            # Optional but recommended
            sudo apt-get install -y asciinema || echo "asciinema not available"
        elif command_exists dnf; then
            echo "Using dnf..."
            sudo dnf install -y python3 python3-pip python3-tkinter
            sudo dnf install -y asciinema || echo "asciinema not available"
        elif command_exists pacman; then
            echo "Using pacman..."
            sudo pacman -S --noconfirm python python-pip tk
            sudo pacman -S --noconfirm asciinema || echo "asciinema not available"
        else
            echo -e "${YELLOW}Could not detect package manager. Please install manually:${NC}"
            echo "  - Python 3"
            echo "  - tkinter (python3-tk)"
            echo "  - asciinema (optional but recommended)"
        fi
    elif [[ "$OS" == "Darwin" ]]; then
        # macOS
        if command_exists brew; then
            echo "Using Homebrew..."
            brew install python-tk
            brew install asciinema || echo "asciinema not available"
        else
            echo -e "${YELLOW}Homebrew not found. Install it from https://brew.sh${NC}"
            exit 1
        fi
    else
        echo -e "${RED}Unsupported OS: $OS${NC}"
        exit 1
    fi
}

# Create installation directory
INSTALL_DIR="$HOME/.local/bin"
RECCLI_DIR="$HOME/.reccli"

echo -e "${YELLOW}Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$RECCLI_DIR"

# Check for Python 3
if ! command_exists python3; then
    echo -e "${RED}Python 3 not found!${NC}"
    install_dependencies
fi

# Check for tkinter
echo -e "${YELLOW}Checking for tkinter...${NC}"
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo -e "${YELLOW}tkinter not found. Installing...${NC}"
    install_dependencies
fi

# Download or copy the main script
SCRIPT_URL="https://raw.githubusercontent.com/willluecke/RecCli/main/reccli.py"
SCRIPT_PATH="$RECCLI_DIR/reccli.py"

if [ -f "reccli.py" ]; then
    echo -e "${GREEN}Using local reccli.py${NC}"
    cp reccli.py "$SCRIPT_PATH"
else
    echo -e "${YELLOW}Downloading reccli.py...${NC}"
    if command_exists curl; then
        curl -sSL "$SCRIPT_URL" -o "$SCRIPT_PATH" || cp reccli.py "$SCRIPT_PATH" 2>/dev/null
    elif command_exists wget; then
        wget -q "$SCRIPT_URL" -O "$SCRIPT_PATH" || cp reccli.py "$SCRIPT_PATH" 2>/dev/null
    else
        echo -e "${RED}Could not download. Please copy reccli.py to $SCRIPT_PATH${NC}"
        exit 1
    fi
fi

# Make executable
chmod +x "$SCRIPT_PATH"

# Create launcher script
LAUNCHER_PATH="$INSTALL_DIR/reccli"
cat > "$LAUNCHER_PATH" << 'EOF'
#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.expanduser("~/.reccli"))
exec(open(os.path.expanduser("~/.reccli/reccli.py")).read())
EOF

chmod +x "$LAUNCHER_PATH"

# Create desktop entry for GUI (Linux only)
if [[ "$OS" == "Linux" ]]; then
    DESKTOP_DIR="$HOME/.local/share/applications"
    mkdir -p "$DESKTOP_DIR"

    cat > "$DESKTOP_DIR/reccli.desktop" << EOF
[Desktop Entry]
Name=RecCli
Comment=One-click terminal recording
Exec=$LAUNCHER_PATH gui
Icon=utilities-terminal
Type=Application
Categories=Development;System;
Terminal=false
EOF

    echo -e "${GREEN}Desktop entry created${NC}"
fi

# Add to PATH if needed
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo -e "${YELLOW}Adding $INSTALL_DIR to PATH...${NC}"
    
    # Detect shell
    if [ -n "$BASH_VERSION" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -n "$ZSH_VERSION" ]; then
        SHELL_RC="$HOME/.zshrc"
    else
        SHELL_RC="$HOME/.profile"
    fi
    
    echo "" >> "$SHELL_RC"
    echo "# Added by clirec installer" >> "$SHELL_RC"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$SHELL_RC"
    
    echo -e "${GREEN}Added to $SHELL_RC${NC}"
    echo -e "${YELLOW}Please restart your terminal or run: source $SHELL_RC${NC}"
fi

# Create aliases (optional)
echo -e "${YELLOW}Creating helpful aliases...${NC}"
ALIASES_FILE="$HOME/.reccli_aliases"
cat > "$ALIASES_FILE" << 'EOF'
# RecCli aliases
alias rec='reccli gui &'
alias recstop='pkill -f reccli'
alias recstats='reccli status'

# Quick launch
alias reccli-start='reccli gui &'
EOF

echo -e "${GREEN}Aliases created in $ALIASES_FILE${NC}"
echo "Add this line to your shell config to use them:"
echo "  source $ALIASES_FILE"

# Test installation
echo ""
echo -e "${YELLOW}Testing installation...${NC}"
if python3 "$SCRIPT_PATH" --help >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Installation successful!${NC}"
else
    echo -e "${RED}❌ Installation test failed${NC}"
    exit 1
fi

# Print usage
echo ""
echo -e "${GREEN}🎉 RecCli installed successfully!${NC}"
echo ""
echo "Usage:"
echo "  reccli gui          # Start GUI mode (floating button)"
echo "  reccli status       # Show recording stats"
echo ""
echo "GUI Mode:"
echo "  - Click the button to start/stop recording"
echo "  - Right-click for menu options"
echo "  - Drag to move the button"
echo ""
echo "Recordings are saved to: ~/.reccli/recordings"
echo ""
echo -e "${YELLOW}Optional: Install asciinema for better recording format:${NC}"
echo "  pip install asciinema"
echo ""
echo "Enjoy recording your CLI sessions! 🎬"
