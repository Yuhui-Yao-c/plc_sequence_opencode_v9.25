@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PLC Web UI

echo.
echo   ============================================
echo     PLC Step Sequence Web UI
echo     URL: http://127.0.0.1:5173
echo     发任务后会弹出 opencode 窗口看实时进度
echo   ============================================
echo.

rem ---- 启动 node server.js ----
echo   Starting node server.js on port 5173...
start "node-server" cmd /c "node server.js"
set /a waited=0
:wait_web
timeout /t 1 /nobreak >nul
set /a waited+=1
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -TimeoutSec 2 -UseBasicParsing) | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% == 0 goto web_ready
if %waited% lss 8 goto wait_web
echo   [ERROR] node server.js failed to start.
echo          Check if 'node' is in PATH.
pause
exit /b 1
:web_ready
echo   node server.js is ready.

rem ---- 打开浏览器 ----
start http://127.0.0.1:5173

echo.
echo   ============================================
echo   前端: http://127.0.0.1:5173
echo   发任务后会弹出 opencode 窗口看实时进度
echo   关闭本窗口停止服务
echo   ============================================
echo.

:loop
timeout /t 3600 /nobreak >nul
goto loop
