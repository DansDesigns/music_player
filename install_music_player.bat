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

:: ── Ensure winget is available (needed to auto-install Python and git) ─────────
echo ------------------------------
echo Checking for winget...
echo ------------------------------
winget --version >nul 2>&1
if %errorlevel% neq 0 (
    echo winget not found. Attempting to install App Installer from Microsoft...
    powershell -NoProfile -Command ^
      "try {" ^
      "  $url = 'https://aka.ms/getwinget';" ^
      "  $out = \"$env:TEMP\AppInstaller.msixbundle\";" ^
      "  Write-Host 'Downloading App Installer...';" ^
      "  Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing;" ^
      "  Write-Host 'Installing App Installer...';" ^
      "  Add-AppxPackage -Path $out;" ^
      "  Write-Host 'Done.';" ^
      "} catch { Write-Host \"Failed: $_\"; exit 1 }"
    if %errorlevel% neq 0 (
        echo.
        echo WARNING: Could not install winget automatically.
        echo Python and git will need to be installed manually if missing.
        echo   Python: https://www.python.org/downloads/
        echo   git:    https://git-scm.com/download/win
        echo.
    ) else (
        winget --version >nul 2>&1
        if %errorlevel% equ 0 (
            echo winget is now available.
        ) else (
            echo winget installed but not yet on PATH. A restart may be needed.
        )
    )
) else (
    for /f "tokens=*" %%v in ('winget --version') do echo Found: winget %%v
)
echo.

:: ── Check / install Python ─────────────────────────────────────────────────────
echo ------------------------------
echo Checking for Python...
echo ------------------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found. Installing via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Could not install Python automatically.
        echo Please install Python 3.10+ manually from https://www.python.org/downloads/
        echo Make sure to tick "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
    for /f "tokens=*" %%i in ('where python 2^>nul') do set "PYTHON_EXE=%%i"
    if not defined PYTHON_EXE (
        echo.
        echo Python installed but not yet on PATH. Please open a new Command Prompt and re-run.
        pause
        exit /b 1
    )
    echo Python installed.
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
    echo ERROR: Python venv module not available. Try: pip install virtualenv
    pause
    exit /b 1
)

:: ── Check / install git ────────────────────────────────────────────────────────
echo ------------------------------
echo Checking for git...
echo ------------------------------
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo git not found. Installing via winget...
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Could not install git automatically.
        echo Please install git manually from https://git-scm.com/download/win
        pause
        exit /b 1
    )
    for /f "tokens=*" %%i in ('where git 2^>nul') do set "GIT_EXE=%%i"
    if not defined GIT_EXE (
        echo.
        echo git installed but not yet on PATH. Please open a new Command Prompt and re-run.
        pause
        exit /b 1
    )
    echo git installed.
) else (
    for /f "tokens=*" %%v in ('git --version') do echo Found: %%v
)
echo.

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

:: ── ffmpeg (needed for MP3 overamplification and duration detection) ──────────
echo ------------------------------
echo Checking for ffmpeg...
echo ------------------------------
ffmpeg -version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=3" %%v in ('ffmpeg -version 2^>^&1 ^| findstr /i "ffmpeg version"') do (
        echo        Found: ffmpeg %%v
        goto :ffmpeg_done
    )
    echo        Found: ffmpeg
) else (
    echo        ffmpeg not found. Installing via winget...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    powershell -NoProfile -ExecutionPolicy Bypass -File "%REFRESH_PS1%" 2>nul
    if exist "%REFRESH_BAT%" call "%REFRESH_BAT%"
    ffmpeg -version >nul 2>&1
    if %errorlevel% equ 0 (
        echo        ffmpeg installed.
    ) else (
        echo        WARNING: ffmpeg not on PATH yet. MP3 overamplification may not work.
        echo          Restart your terminal after install, or get ffmpeg from:
        echo          https://ffmpeg.org/download.html
    )
)
:ffmpeg_done
echo.

:: ── Temp files for PATH refresh after winget installs ───────────────────────
set "REFRESH_PS1=%TEMP%\wp_refresh.ps1"
set "REFRESH_BAT=%TEMP%\wp_refresh_path.bat"
echo $s = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -Name Path -EA SilentlyContinue).Path  > "%REFRESH_PS1%"
echo $u = (Get-ItemProperty 'HKCU:\Environment' -Name Path -EA SilentlyContinue).Path                                                   >> "%REFRESH_PS1%"
echo if ($u) { $p = "$s;$u" } else { $p = $s }                                                                                          >> "%REFRESH_PS1%"
echo Set-Content -Path ([System.IO.Path]::GetTempPath() + 'wp_refresh_path.bat') -Value "@set PATH=$p"                                   >> "%REFRESH_PS1%"

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
