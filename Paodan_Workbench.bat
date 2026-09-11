@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\open_workbench.py
if errorlevel 1 (
  echo.
  echo 启动失败，请确认已安装 Python 和依赖。
  pause
)
