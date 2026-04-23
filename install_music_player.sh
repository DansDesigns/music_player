#!/bin/bash
set -e

echo "=============================="
echo " WavePlayer Installer"
echo "=============================="
echo ""

# ── Install dir is wherever this script lives ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
RUN_SCRIPT="$SCRIPT_DIR/run_music_player.sh"

echo "Install directory: $SCRIPT_DIR"
echo ""

# ── Detect package manager ────────────────────────────────────────────────────
if command -v nala &>/dev/null; then
    PKG_INSTALL="sudo nala install -y"
    PKG_UPDATE="sudo nala update"
elif command -v apt-get &>/dev/null; then
    PKG_INSTALL="sudo apt-get install -y"
    PKG_UPDATE="sudo apt-get update"
elif command -v dnf &>/dev/null; then
    PKG_INSTALL="sudo dnf install -y"
    PKG_UPDATE=""
elif command -v pacman &>/dev/null; then
    PKG_INSTALL="sudo pacman -S --noconfirm"
    PKG_UPDATE="sudo pacman -Sy"
else
    PKG_INSTALL=""
    PKG_UPDATE=""
    echo "WARNING: No supported package manager found (apt/nala/dnf/pacman)."
    echo "         System packages will not be installed automatically."
fi

# ── Install system packages including Python ──────────────────────────────────
echo "------------------------------"
echo "Installing system packages..."
echo "------------------------------"

if [ -n "$PKG_INSTALL" ]; then
    # Update package lists first (skip if it fails — offline etc.)
    if [ -n "$PKG_UPDATE" ]; then
        $PKG_UPDATE || echo "WARNING: Package list update failed, continuing anyway..."
    fi

    if command -v apt-get &>/dev/null || command -v nala &>/dev/null; then
        $PKG_INSTALL \
            python3 python3-pip python3-venv python3-tk \
            espeak espeak-ng libespeak-ng1 \
            git
    elif command -v dnf &>/dev/null; then
        $PKG_INSTALL \
            python3 python3-pip python3-tkinter \
            espeak espeak-ng \
            git
    elif command -v pacman &>/dev/null; then
        $PKG_INSTALL \
            python python-pip tk \
            espeak-ng \
            git
    fi
fi

echo ""

# ── Verify Python 3.10+ is available ─────────────────────────────────────────
echo "------------------------------"
echo "Checking Python version..."
echo "------------------------------"

PYTHON_CMD=""
for cmd in python3 python3.12 python3.11 python3.10 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON_CMD="$cmd"
            echo "Found: $cmd $("$cmd" --version 2>&1)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo ""
    echo "ERROR: Python 3.10 or later is required but was not found."
    echo "Please install it manually:"
    echo "  Ubuntu/Debian: sudo apt-get install python3"
    echo "  Fedora:        sudo dnf install python3"
    echo "  Arch:          sudo pacman -S python"
    echo "  Other:         https://www.python.org/downloads/"
    exit 1
fi

echo ""

# ── Check venv module ─────────────────────────────────────────────────────────
if ! "$PYTHON_CMD" -c "import venv" &>/dev/null; then
    echo "ERROR: Python venv module not found."
    echo "Install it with: sudo apt-get install python3-venv"
    exit 1
fi

# ── Create virtual environment ────────────────────────────────────────────────
echo "------------------------------"
echo "Creating virtual environment..."
echo "------------------------------"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    echo "Created: $VENV_DIR"
else
    echo "Reusing existing venv: $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo ""

# ── Python dependencies ───────────────────────────────────────────────────────
echo "------------------------------"
echo "Installing Python dependencies..."
echo "------------------------------"
pip install --upgrade pip
pip install -r "$SCRIPT_DIR/requirements.txt"
echo ""

# ── Make run script executable ───────────────────────────────────────────────
chmod +x "$RUN_SCRIPT"

# ── .desktop file ─────────────────────────────────────────────────────────────
echo "------------------------------"
echo "Creating application shortcut..."
echo "------------------------------"

APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"

ICON="audio-x-generic"
for ext in png svg ico xpm; do
    if [ -f "$SCRIPT_DIR/icon.$ext" ]; then
        ICON="$SCRIPT_DIR/icon.$ext"
        break
    fi
done

cat > "$APP_DIR/waveplayer.desktop" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=WavePlayer
GenericName=Media Player
Comment=Voice-controlled music and video player
Exec=$RUN_SCRIPT %f
Icon=$ICON
Terminal=false
Categories=AudioVideo;Audio;Player;Music;
Keywords=music;player;voice;audio;media;wave;
StartupNotify=true
MimeType=audio/mpeg;audio/ogg;audio/flac;audio/wav;audio/x-m4a;video/mp4;video/x-matroska;
DESKTOP

chmod +x "$APP_DIR/waveplayer.desktop"
command -v update-desktop-database &>/dev/null && \
    update-desktop-database "$APP_DIR" 2>/dev/null || true

echo "Shortcut: $APP_DIR/waveplayer.desktop"
echo "Icon:     $ICON"
echo ""
echo "=============================="
echo " Installation complete!"
echo ""
echo " WavePlayer will appear in your application launcher."
echo " To run it directly:  $RUN_SCRIPT"
echo "=============================="
echo ""

exec "$RUN_SCRIPT"
