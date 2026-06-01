@echo off
cd /d E:\redMuse\XHS_viral_notes

REM 激活虚拟环境（如使用 venv）
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo [%DATE% %TIME%] Starting ARQ Worker... >> logs\worker.log
python -m backend.app.infrastructure.queue.runner >> logs\worker.log 2>&1
echo [%DATE% %TIME%] ARQ Worker exited with code %ERRORLEVEL% >> logs\worker.log
