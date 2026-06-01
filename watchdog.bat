@echo off
REM Watchdog: check ARQ Worker is alive via full command line, restart if not.
REM Register with Task Scheduler to run every 5 minutes.

set LOG=E:\redMuse\XHS_viral_notes\logs\watchdog.log

powershell -NoProfile -Command ^
  "if (Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like '*queue.runner*' }) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo [%DATE% %TIME%] Worker offline, restarting... >> %LOG%
    start "" /B E:\redMuse\XHS_viral_notes\start-worker.bat
) else (
    echo [%DATE% %TIME%] Worker alive. >> %LOG%
)
