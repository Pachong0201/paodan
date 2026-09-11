@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo 正在创建 Paodan Workbench 桌面图标...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_workbench_shortcut.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] 创建失败。请右键本文件，选择“以管理员身份运行”，或把错误信息发给开发者。
    pause
    exit /b 1
)
echo.
echo 完成。以后双击桌面上的 "Paodan 工作台" 图标即可启动。
pause
