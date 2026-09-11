@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo Paodan V5.0.2 Local Workbench
echo ============================================
echo.

if exist ".venv\Scripts\python.exe" (
    echo [OK] 使用项目虚拟环境 .venv
    ".venv\Scripts\python.exe" scripts\open_workbench.py
    goto :done
)

where py >nul 2>nul
if not errorlevel 1 (
    echo [OK] 使用 Windows py launcher
    py -3 scripts\open_workbench.py
    goto :done
)

where python >nul 2>nul
if not errorlevel 1 (
    echo [OK] 使用全局 python
    python scripts\open_workbench.py
    goto :done
)

echo [ERROR] 没有找到 Python。
echo 请先安装 Python 3.11+，然后双击 install.bat。
pause
exit /b 1

:done
if errorlevel 1 (
    echo.
    echo [ERROR] Workbench 启动失败。请把上面的错误信息发给开发者。
    pause
)
