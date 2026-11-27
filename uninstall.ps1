param(
    [switch]$PurgeData,
    [string]$InstallDir = ''
)
$ErrorActionPreference = 'Stop'

$legacyInstall = Join-Path $env:LOCALAPPDATA 'Programs\HelldiversTactical'
$legacyData = Join-Path $env:LOCALAPPDATA 'HelldiversTactical'

if (-not $InstallDir) {
    if (Test-Path (Join-Path $PSScriptRoot 'HelldiversTactical.exe')) {
        $InstallDir = $PSScriptRoot
    } elseif (Test-Path 'D:\工具\HelldiversTactical\HelldiversTactical.exe') {
        $InstallDir = 'D:\工具\HelldiversTactical'
    } else {
        $InstallDir = $legacyInstall
    }
}

function Remove-Shortcut([string]$Path) {
    if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction SilentlyContinue }
}

function Remove-Tree([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    $cmd = "timeout /t 2 /nobreak >nul & rmdir /s /q `"$Path`""
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden
}

$programs = [Environment]::GetFolderPath('Programs')
$startup = [Environment]::GetFolderPath('Startup')
Remove-Shortcut (Join-Path $programs 'Helldivers Tactical Command.lnk')
Remove-Shortcut (Join-Path $startup 'Helldivers Tactical Command.lnk')

Get-Process -Name 'HelldiversTactical' -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name 'msedgewebview2' -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -and ($_.Path -like '*HelldiversTactical*') } catch { $false }
} | Stop-Process -Force
Start-Sleep -Milliseconds 900

if ($PurgeData) {
    Remove-Tree (Join-Path $InstallDir 'userdata')
    Remove-Tree $legacyData
    Write-Host '已清除用户设置、缓存与告警状态。'
}

if ((Test-Path $InstallDir) -and ($InstallDir -ne $PSScriptRoot)) { Remove-Tree $InstallDir }
if ((Test-Path $legacyInstall) -and ($legacyInstall -ne $InstallDir) -and ($legacyInstall -ne $PSScriptRoot)) { Remove-Tree $legacyInstall }

Write-Host '已移除开机自启与开始菜单项。' -ForegroundColor Yellow
Write-Host "应用目录将在后台删除: $InstallDir"
Start-Sleep -Seconds 2