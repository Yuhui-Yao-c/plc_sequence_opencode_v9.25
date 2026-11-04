@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js 18+ was not found. Install Node.js and reopen this window.
  pause
  exit /b 1
)
title PLC Workbench
node server.js --open
set "plc_exit=%errorlevel%"
if not "%plc_exit%"=="0" echo [ERROR] Workbench exited with code %plc_exit%. See details above.
pause
exit /b %plc_exit%
