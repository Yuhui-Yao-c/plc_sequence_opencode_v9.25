@echo off
setlocal
cd /d "%~dp0"

python --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.8+ and add it to PATH.
    pause
    exit /b 1
)

python -c "import flask,lxml,openpyxl,pandas" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing project Python dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 goto install_failed
)

echo.
echo Starting PLC Sequence Agent Web Console...
echo The default address is http://127.0.0.1:5173
echo OpenCode CLI must be installed and authenticated separately.
echo.
python web_app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Web console exited with an error.
    pause
)
goto :eof

:install_failed
echo.
echo [ERROR] Dependency installation failed. Check Python/pip and network access.
pause
exit /b 1
