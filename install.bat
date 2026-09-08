@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Paodan V4.2.1 install ===
where python >nul 2>nul
if errorlevel 1 (
  echo 需要先安装 Python 3.11+
  pause
  exit /b 1
)
if not exist ".venv" (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt -c constraints.txt
echo === install complete ===
echo 启动命令：python scripts\open_dashboard.py
pause
