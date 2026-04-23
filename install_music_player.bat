@echo off
setlocal disableDelayedExpansion

echo ==============================
echo  WavePlayer Installer
echo ==============================
echo.

:: ── Install dir is wherever this script lives ─────────────────────────────────
set "INSTALL_DIR=%~dp0"
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
echo Install directory: %INSTALL_DIR%
echo.

:: ── Check for Python ──────────────────────────────────────────────────────────
echo ------------------------------
echo Checking for Python...
echo ------------------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found on PATH.
    echo.
    echo Attempting to install Python via winget...
    winget --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo winget not found. Attempting to install App Installer from Microsoft...
        echo.
        :: Download the App Installer (winget) msixbundle via PowerShell
        powershell -NoProfile -Command ^
          "try { " ^
          "  $url = 'https://aka.ms/getwinget'; " ^
          "  $out = "$env:TEMP\AppInstaller.msixbundle"; " ^
          "  Write-Host 'Downloading App Installer...'; " ^
          "  Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; " ^
          "  Write-Host 'Installing App Installer...'; " ^
          "  Add-AppxPackage -Path $out; " ^
          "  Write-Host 'App Installer installed successfully.'; " ^
          "} catch { Write-Host "Failed: $_"; exit 1 }"
        if %errorlevel% neq 0 (
            echo.
            echo ERROR: Could not install winget automatically.
            echo Please install Python 3.10 or later manually from:
            echo   https://www.python.org/downloads/
            echo Make sure to tick "Add Python to PATH" during installation.
            echo Then re-run this installer.
            pause
            exit /b 1
        )
        :: Verify winget is now available
        winget --version >nul 2>&1
        if %errorlevel% neq 0 (
            echo.
            echo App Installer was installed but winget is still not on PATH.
            echo Please restart your computer and re-run this installer.
            pause
            exit /b 1
        )
        echo winget is now available.
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Automatic Python installation failed.
        echo Please install Python manually from https://www.python.org/downloads/
        echo Make sure to tick "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
    echo Python installed. Refreshing environment...
    :: Refresh PATH so python is available in this session
    for /f "tokens=*" %%i in ('where python 2^>nul') do set "PYTHON_EXE=%%i"
    if not defined PYTHON_EXE (
        echo.
        echo Python was installed but cannot be found on PATH yet.
        echo Please close this window, open a new Command Prompt, and re-run the installer.
        pause
        exit /b 1
    )
) else (
    for /f "tokens=*" %%v in ('python --version') do echo Found: %%v
)
echo.

:: ── Check Python version is 3.10+ ─────────────────────────────────────────────
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
if %PY_MAJOR% lss 3 (
    echo ERROR: Python 3.10 or later is required. Found %PY_VER%.
    pause
    exit /b 1
)
if %PY_MAJOR% equ 3 if %PY_MINOR% lss 10 (
    echo ERROR: Python 3.10 or later is required. Found %PY_VER%.
    pause
    exit /b 1
)

:: ── Check venv module is available ────────────────────────────────────────────
python -c "import venv" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python venv module is not available.
    echo On some systems you may need to install it separately.
    echo Try: pip install virtualenv
    pause
    exit /b 1
)

:: ── Create virtual environment ────────────────────────────────────────────────
echo ------------------------------
echo Creating virtual environment...
echo ------------------------------
if exist "%INSTALL_DIR%\.venv" rmdir /s /q "%INSTALL_DIR%\.venv"
python -m venv "%INSTALL_DIR%\.venv"
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)
call "%INSTALL_DIR%\.venv\Scripts\activate.bat"
echo Done.
echo.

:: ── Install dependencies ──────────────────────────────────────────────────────
echo ------------------------------
echo Installing Python dependencies...
echo ------------------------------
pip install --upgrade pip
pip install -r "%INSTALL_DIR%\requirements.txt"
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo.

:: ── Resolve icon ──────────────────────────────────────────────────────────────
set "ICON_PATH=%INSTALL_DIR%\icon.ico"
if not exist "%ICON_PATH%" set "ICON_PATH=%INSTALL_DIR%\.venv\Scripts\python.exe"

:: ── Shortcuts pointing to run_music_player.bat ────────────────────────────────
echo ------------------------------
echo Creating shortcuts...
echo ------------------------------
set "TARGET=%INSTALL_DIR%\run_music_player.bat"
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\WavePlayer.lnk"
set "DESKTOP=%USERPROFILE%\Desktop\WavePlayer.lnk"

powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%START_MENU%'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%INSTALL_DIR%'; $s.IconLocation='%ICON_PATH%'; $s.Description='WavePlayer - voice-controlled media player'; $s.WindowStyle=7; $s.Save()"
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%DESKTOP%'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%INSTALL_DIR%'; $s.IconLocation='%ICON_PATH%'; $s.Description='WavePlayer - voice-controlled media player'; $s.WindowStyle=7; $s.Save()"

if exist "%START_MENU%" echo Start Menu: %START_MENU%
if exist "%DESKTOP%"     echo Desktop:    %DESKTOP%

echo.
echo ==============================
echo  Installation complete!
echo  WavePlayer is in your Start Menu and on the Desktop.
echo ==============================
echo.

call "%TARGET%"
