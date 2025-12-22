@echo off
chcp 65001 >nul
echo =========================================
echo   小红书爆文Agent - OCR修复工具
echo =========================================
echo.

echo 当前PaddlePaddle版本：
pip show paddlepaddle 2>nul || echo 未安装

echo.
echo 当前PaddleOCR版本：
pip show paddleocr 2>nul || echo 未安装

echo.
echo =========================================
echo.
echo 【方案1】升级到最新版本（推荐）
echo PaddlePaddle 3.2.1 + 最新PaddleOCR
echo （最新版本已修复兼容性问题）
echo.
echo 【方案2】使用2.6.1稳定版本
echo PaddlePaddle 2.6.1 + PaddleOCR 2.7.0.3
echo （稳定但可能仍有兼容性问题）
echo.

set /p choice="请选择修复方案 (1/2): "

if "%choice%"=="1" (
    echo.
    echo 步骤1: 卸载当前版本...
    pip uninstall -y paddlepaddle paddleocr

    echo.
    echo 步骤2: 安装最新版本...
    echo   - PaddlePaddle 3.2.1
    echo   - 最新 PaddleOCR
    pip install paddlepaddle==3.2.1
    pip install --upgrade paddleocr

    echo.
    echo 步骤3: 验证安装...
    python test_ocr.py

    if %errorlevel% equ 0 (
        echo.
        echo ✅ OCR修复成功！
        echo.
        echo 您现在可以：
        echo 1. 重启 viral_app.py 服务
        echo 2. 进行新的爆文分析任务
        echo 3. 查看封面文字识别结果
    ) else (
        echo.
        echo ❌ 方案1失败，可以尝试方案2
    )

) else if "%choice%"=="2" (
    echo.
    echo 步骤1: 卸载当前版本...
    pip uninstall -y paddlepaddle paddleocr

    echo.
    echo 步骤2: 安装2.6.1版本...
    echo   - PaddlePaddle 2.6.1
    echo   - PaddleOCR 2.7.0.3
    pip install paddlepaddle==2.6.1 paddleocr==2.7.0.3

    echo.
    echo 步骤3: 验证安装...
    python test_ocr.py

    if %errorlevel% equ 0 (
        echo.
        echo ✅ OCR修复成功！
        echo.
        echo 您现在可以：
        echo 1. 重启 viral_app.py 服务
        echo 2. 进行新的爆文分析任务
        echo 3. 查看封面文字识别结果
    ) else (
        echo.
        echo ❌ OCR修复失败，可能需要手动排查
    )
) else (
    echo.
    echo 无效选择
)

echo.
pause
