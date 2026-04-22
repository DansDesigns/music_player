@echo off
setlocal disableDelayedExpansion
call ".venv\Scripts\activate.bat"
python music_player.py
pause