$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$IconSrc = Join-Path $Root 'assets\paodan_workbench.ico'

$Distro = 'Ubuntu'
$ProjectWsl = ''
if ($Root -match '^\\\\wsl(?:\.localhost)?\\([^\\]+)\\(.+)$') {
    $Distro = $Matches[1]
    $rel = $Matches[2] -replace '\\', '/'
    $ProjectWsl = '/' + $rel.TrimStart('/')
}
if (-not $ProjectWsl) {
    $ProjectWsl = '/home/' + $env:USERNAME + '/paodan'
}

$IconDir = Join-Path $env:LOCALAPPDATA 'Paodan'
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null
$Icon = Join-Path $IconDir 'paodan_workbench.ico'
Copy-Item -Force $IconSrc $Icon

$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'Paodan 工作台.lnk'
$WslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'
$WslArgs = "-d $Distro -e bash -lc `"cd $ProjectWsl && .venv/bin/python scripts/open_workbench.py`""

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($ShortcutPath)
$sc.TargetPath = $WslExe
$sc.Arguments = $WslArgs
$sc.WorkingDirectory = $env:USERPROFILE
$sc.IconLocation = "$Icon,0"
$sc.Description = 'Paodan V5.0.2 Local Workbench (WSL)'
$sc.Save()

Write-Host "[OK] 已创建桌面图标：$ShortcutPath"
Write-Host "     WSL distro: $Distro"
Write-Host "     project:    $ProjectWsl"
