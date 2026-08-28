# 注册 Windows 计划任务：每 15 分钟运行一次 monitor.py
$ErrorActionPreference = "Stop"
$workdir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python -replace "python\.exe$", "pythonw.exe" }
$taskName = "FeishuDataMonitor"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$workdir\monitor.py`"" -WorkingDirectory $workdir
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

$registered = $false
foreach ($dur in @([TimeSpan]::MaxValue, (New-TimeSpan -Days 3650))) {
    try {
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration $dur
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Description "派单中心新工单监控并推送飞书" -Force | Out-Null
        $registered = $true
        break
    } catch {
        Write-Host "注册方式失败（$($_.Exception.Message)），尝试下一种..."
    }
}

if (-not $registered) {
    # 最终回退：schtasks 经典命令（重复周期原生支持，但无 StartWhenAvailable）
    schtasks /Create /F /TN $taskName /SC MINUTE /MO 15 `
        /TR "`"$pythonw`" `"$workdir\monitor.py`""
}

# 确保错过的检查在下次开机后补跑（schtasks 回退路径下单独补设置）
try {
    $t = Get-ScheduledTask -TaskName $taskName
    if (-not $t.Settings.StartWhenAvailable) {
        $t.Settings.StartWhenAvailable = $true
        $t | Set-ScheduledTask | Out-Null
    }
} catch {
    Write-Host "提示：StartWhenAvailable 补设置失败（$($_.Exception.Message)）"
}

Write-Host "OK: 计划任务 $taskName 已注册，每 15 分钟执行一次"
