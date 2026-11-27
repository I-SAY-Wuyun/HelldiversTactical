param(
    [switch]$Uninstall,
    [switch]$NoStart,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\HelldiversTactical')
)
$ErrorActionPreference = 'Stop'

function New-Shortcut([string]$Path, [string]$Target, [string]$WorkingDirectory, [string]$Icon, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$Icon,0"
    $shortcut.Description = $Description
    $shortcut.Save()
}

function Remove-Shortcut([string]$Path) {
    if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction SilentlyContinue }
}

$desktop = [Environment]::GetFolderPath('Desktop')
$startup = [Environment]::GetFolderPath('Startup')
$desktopLnk = Join-Path $desktop 'Helldivers Tactical Command.lnk'
$startupLnk = Join-Path $startup 'Helldivers Tactical Command.lnk'

if ($Uninstall) {
    Get-Process -Name 'HelldiversTactical' -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-Shortcut $desktopLnk
    Remove-Shortcut $startupLnk
    if (Test-Path $InstallDir) {
        $cmd = "timeout /t 2 /nobreak >nul & rmdir /s /q `"$InstallDir`""
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden
    }
    Write-Host '已移除开机自启与桌面快捷方式。' -ForegroundColor Yellow
    Write-Host '应用目录将在后台删除；用户缓存保留在 %LOCALAPPDATA%\HelldiversTactical。'
    Start-Sleep -Seconds 2
    exit 0
}

$source = Join-Path $PSScriptRoot 'dist\HelldiversTactical'
if (-not (Test-Path (Join-Path $source 'HelldiversTactical.exe'))) {
    # Allow running install.ps1 from inside the packaged folder itself.
    if (Test-Path (Join-Path $PSScriptRoot 'HelldiversTactical.exe')) {
        $source = $PSScriptRoot
    } else {
        throw '找不到 dist\HelldiversTactical\HelldiversTactical.exe，请先运行 build.ps1。'
    }
}

try {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
} catch {
    $InstallDir = Join-Path $PSScriptRoot 'App'
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

Get-Process -Name 'HelldiversTactical' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Write-Host "安装到: $InstallDir"
Copy-Item (Join-Path $source '*') $InstallDir -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') $InstallDir -Force -ErrorAction SilentlyContinue
$uninstallBat = Join-Path $PSScriptRoot '卸载.bat'
if (Test-Path $uninstallBat) { Copy-Item $uninstallBat $InstallDir -Force }

$exe = Join-Path $InstallDir 'HelldiversTactical.exe'
$config = Join-Path $InstallDir 'HelldiversTactical.exe.config'
if (-not (Test-Path $config)) {
    Copy-Item (Join-Path $PSScriptRoot 'packaging\HelldiversTactical.exe.config') $config -Force
}
$icon = Join-Path $InstallDir '_internal\assets\icon.ico'
if (-not (Test-Path $icon)) { $icon = $exe }

New-Shortcut $desktopLnk $exe $InstallDir $icon 'HELLDIVERS 2 // TACTICAL COMMAND'
New-Shortcut $startupLnk $exe $InstallDir $icon 'HELLDIVERS 2 // TACTICAL COMMAND - 开机自启'

Write-Host '桌面快捷方式与开机启动项已创建。' -ForegroundColor Green
Write-Host "启动项: $startupLnk"
if (-not $NoStart) {
    Start-Process -FilePath $exe -WorkingDirectory $InstallDir
}
