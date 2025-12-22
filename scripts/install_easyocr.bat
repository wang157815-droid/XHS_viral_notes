@echo off
chcp 65001 >nul
echo =========================================
echo   安装 EasyOCR（替代PaddleOCR）
echo =========================================
echo.
echo EasyOCR 优势：
echo ✓ 兼容性好，适配多种系统
echo ✓ 安装简单，无复杂依赖
echo ✓ 支持80+种语言
echo ✓ 准确率高，效果出色
echo.
echo 即将执行：
echo   pip install easyocr
echo.

set /p confirm="是否继续安装？(y/n): "

if /i "%confirm%"=="y" (
    echo.
    echo 正在安装 EasyOCR...
    pip install easyocr

    echo.
    echo 安装完成！测试OCR功能...
    python test_ocr.py

    if %errorlevel% equ 0 (
        echo.
        echo =========================================
        echo   ✅ EasyOCR 安装成功！
        echo =========================================
        echo.
        echo 下一步：
        echo 1. 重启 viral_app.py 服务
        echo 2. 进行爆文分析
        echo 3. 系统会自动使用EasyOCR识别封面文字
        echo.
    ) else (
        echo.
        echo ❌ 安装失败，请检查错误信息
    )
) else (
    echo.
    echo 取消安装
)

echo.
pause
