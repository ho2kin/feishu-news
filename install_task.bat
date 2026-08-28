@echo off
chcp 65001 >nul
echo 正在注册 Windows 计划任务...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_task.ps1"
pause
