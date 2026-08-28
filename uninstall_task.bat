@echo off
chcp 65001 >nul
echo 正在停止并删除计划任务 FeishuDataMonitor ...
schtasks /End /TN FeishuDataMonitor >nul 2>&1
schtasks /Delete /TN FeishuDataMonitor /F >nul 2>&1
if %errorlevel%==0 (
    echo OK: 计划任务 FeishuDataMonitor 已停止并删除，监控不再自动运行。
) else (
    echo 任务不存在或删除失败（可能本来就没有注册）。
)
pause
