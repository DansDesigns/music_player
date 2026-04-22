#!/bin/bash
set -e

echo "=============================="
echo " Wave Music Player Installer"
echo "=============================="
echo""
echo "-------------------------------"
echo "Installing system packages..."
echo "-------------------------------"
sudo apt install nala nala -y
#sudo nala update
sudo nala install -y \
    git alacritty htop python3-venv \
    python3 python3-pip python3-tk \
    espeak espeak-ng libespeak-ng1

echo""
echo "-------------------------------"
echo "Creating virtual environment..."
echo "-------------------------------"

if [ ! -d "$HOME/music_player/.venv" ]; then
    python3 -m venv "$HOME/music_player/.venv"
fi

source "$HOME/music_player/.venv/bin/activate"

echo""
echo "-------------------------------"
echo "Installing Python dependencies..."
echo "-------------------------------"
pip3 install --upgrade pip
pip3 install pyttsx3 vosk numpy pygame psutil opencv-python pynput qtile qtile-extras mypy

pip3 install -r requirements.txt

echo""
echo "=== Installation Complete ==="
echo""

python music_player.py