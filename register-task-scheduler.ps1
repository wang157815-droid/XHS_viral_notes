# register-task-scheduler.ps1
# 以管理员权限运行此脚本，一次性注册两个 Task Scheduler 任务：
#   1. RedMuse\ARQWorker   — 开机延迟 2 分钟后自启 Worker
#   2. RedMuse\ARQWatchdog — 每 5 分钟检查 Worker 是否存活

$ProjectDir = "E:\redMuse\XHS_viral_notes"

# 检查是否以管理员身份运行
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "请以管理员身份运行此脚本！"
    exit 1
}

# 创建任务组文件夹
$null = New-Item -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tree\RedMuse" -Force -ErrorAction SilentlyContinue

# ---------- 任务1：开机自启 Worker ----------
$action1  = New-ScheduledTaskAction -Execute "$ProjectDir\start-worker.bat"
$trigger1 = New-ScheduledTaskTrigger -AtStartup
# 延迟 2 分钟（等网络和 Redis 就绪）
$trigger1.Delay = "PT2M"
$settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "RedMuse\ARQWorker" `
    -Action $action1 `
    -Trigger $trigger1 `
    -Settings $settings1 `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "[OK] 已注册 RedMuse\ARQWorker（开机延迟 2 分钟自启）"

# ---------- 任务2：每 5 分钟 Watchdog ----------
$action2  = New-ScheduledTaskAction -Execute "$ProjectDir\watchdog.bat"
$trigger2 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)
$settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "RedMuse\ARQWatchdog" `
    -Action $action2 `
    -Trigger $trigger2 `
    -Settings $settings2 `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "[OK] 已注册 RedMuse\ARQWatchdog（每 5 分钟巡检）"
Write-Host ""
Write-Host "完成！可在任务计划程序 -> Task Scheduler Library -> RedMuse 中查看。"
