# 注册 Windows 计划任务：每 5 分钟运行一次 monitor.py
$ErrorActionPreference = "Stop"
$workdir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python -replace "python\.exe$", "pythonw.exe" }
$taskName = "FeishuDataMonitor"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$workdir\monitor.py`"" -WorkingDirectory $workdir
# 不开启 StartWhenAvailable：关机期间错过的检查不补跑，开机后从当前时间按周期正常执行
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

$registered = $false
foreach ($dur in @([TimeSpan]::MaxValue, (New-TimeSpan -Days 3650))) {
    try {
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration $dur
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Description "派单中心新工单监控并推送飞书（纯接口模式）" -Force | Out-Null
        $registered = $true
        break
    } catch {
        Write-Host "注册方式失败（$($_.Exception.Message)），尝试下一种..."
    }
}

if (-not $registered) {
    # 最终回退：schtasks 经典命令
    schtasks /Create /F /TN $taskName /SC MINUTE /MO 5 `
        /TR "`"$pythonw`" `"$workdir\monitor.py`""
}

Write-Host "OK: 计划任务 $taskName 已注册，每 5 分钟执行一次（错过不补跑）"
