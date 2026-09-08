@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\open_dashboard.py
pause
