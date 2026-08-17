@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   桌宠 一键打包脚本（仅开发者机器需要）
echo ============================================
echo.
echo [1/2] 检查构建依赖（PySide6 / Pillow / PyInstaller）...
python -m pip install -r requirements.txt || (echo 依赖安装失败，请检查网络或 Python 环境 & pause & exit /b 1)
echo.
echo [2/2] 开始打包...
python scripts\build.py
pause
