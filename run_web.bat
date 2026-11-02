@echo off
rem PLC 步序采集工作流 - Web 界面启动脚本
cd /d "%~dp0"
title PLC Web UI
echo.
echo   PLC 步序采集工作流 Web 界面
echo   启动后浏览器自动打开 http://127.0.0.1:5173
echo   关闭此窗口即停止服务
echo.
python web_app.py
pause
