@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "TARGET=%ROOT%\open_workbench.bat"
set "ICON=%ROOT%\assets\paodan_workbench.ico"
set "SHORTCUT=%USERPROFILE%\Desktop\Paodan 工作台.lnk"

if not exist "%TARGET%" (
    echo [ERROR] 未找到 %TARGET%
    pause
    exit /b 1
)
if not exist "%ICON%" (
    echo [ERROR] 未找到图标 %ICON%
    pause
    exit /b 1
)

echo 正在创建桌面快捷方式...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%ROOT%'; $s.IconLocation = '%ICON%,0'; $s.Description = 'Paodan V5.0.2 Local Workbench'; $s.Save()"
if errorlevel 1 (
    echo.
    echo [ERROR] 创建快捷方式失败。请右键此文件，选择“以管理员身份运行”。
    pause
    exit /b 1
)

echo.
echo [OK] 已创建桌面图标：Paodan 工作台
echo 以后只要双击桌面上的 "Paodan 工作台" 图标即可启动。
pause
