param(
    [switch]$PurgeData,
    [string]$InstallDir = ''
)
$ErrorActionPreference = 'Stop'

if (-not $InstallDir) {
    if (Test-Path (Join-Path $PSScriptRoot 'HelldiversTactical.exe')) {
        $InstallDir = $PSScriptRoot
    } else {
        $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\HelldiversTactical'
    }
}

function Remove-Shortcut([string]$Path) {
    if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction SilentlyContinue }
}

$desktop = [Environment]::GetFolderPath('Desktop')
$startup = [Environment]::GetFolderPath('Startup')
Remove-Shortcut (Join-Path $desktop 'Helldivers Tactical Command.lnk')
Remove-Shortcut (Join-Path $startup 'Helldivers Tactical Command.lnk')

Get-Process -Name 'HelldiversTactical' -ErrorAction SilentlyContinue | Stop-Process -Force

if ($PurgeData) {
    $data = Join-Path $env:LOCALAPPDATA 'HelldiversTactical'
    if (Test-Path $data) { Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Host '已清除用户缓存与告警状态。'
}

if (Test-Path $InstallDir) {
    $cmd = "timeout /t 2 /nobreak >nul & rmdir /s /q `"$InstallDir`""
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden
}

Write-Host '已移除开机自启与桌面快捷方式。' -ForegroundColor Yellow
Write-Host '应用目录将在后台删除。'
Start-Sleep -Seconds 2
