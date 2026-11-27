param(
    [switch]$Uninstall,
    [switch]$NoStart,
    [string]$InstallDir = ''
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

function Stop-AppProcess {
    Get-Process -Name 'HelldiversTactical' -ErrorAction SilentlyContinue | Stop-Process -Force
    # Only the WebView2 helpers that belong to this app; leave other apps alone.
    Get-Process -Name 'msedgewebview2' -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and ($_.Path -like '*HelldiversTactical*') } catch { $false }
    } | Stop-Process -Force
    Start-Sleep -Milliseconds 900
}

function Remove-Tree([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    $cmd = "timeout /t 2 /nobreak >nul & rmdir /s /q `"$Path`""
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden
}

# Default location: keep the app off the system drive when a D: data drive exists.
if (-not $InstallDir) {
    if (Test-Path 'D:\') {
        $InstallDir = 'D:\工具\HelldiversTactical'
    } else {
        $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\HelldiversTactical'
    }
}

$legacyInstall = Join-Path $env:LOCALAPPDATA 'Programs\HelldiversTactical'
$legacyData = Join-Path $env:LOCALAPPDATA 'HelldiversTactical'
$dataDir = Join-Path $InstallDir 'userdata'

$startup = [Environment]::GetFolderPath('Startup')
$programs = [Environment]::GetFolderPath('Programs')
# No desktop icon by design: the widget lives in the Start menu and the tray.
$startLnk = Join-Path $programs 'Helldivers Tactical Command.lnk'
$startupLnk = Join-Path $startup 'Helldivers Tactical Command.lnk'

if ($Uninstall) {
    Stop-AppProcess
    Remove-Shortcut $startupLnk
    Remove-Shortcut $startLnk
    foreach ($dir in @($InstallDir, $legacyInstall)) {
        if ((Test-Path $dir) -and ($dir -ne $PSScriptRoot)) { Remove-Tree $dir }
    }
    if (Test-Path $legacyData) { Remove-Tree $legacyData }
    Write-Host '已移除开机自启与开始菜单项。' -ForegroundColor Yellow
    Write-Host "应用目录与用户数据（$InstallDir）将在后台删除。"
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

Stop-AppProcess
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

# Portable user data: the app keeps settings/cache/WebView2 profile in this folder.
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
if (Test-Path $legacyData) {
    $existing = @(Get-ChildItem $dataDir -Force -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 0) {
        robocopy $legacyData $dataDir /E /MOVE /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Write-Host "警告: 旧数据未完全迁移 (robocopy $LASTEXITCODE)，仍保留在 $legacyData" -ForegroundColor Yellow
        } else {
            Write-Host '已把 %LOCALAPPDATA% 里的旧设置/缓存迁移到 userdata。' -ForegroundColor Green
        }
        if ((Test-Path $legacyData) -and -not (Get-ChildItem $legacyData -Force -ErrorAction SilentlyContinue)) {
            Remove-Item $legacyData -Force -Recurse -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "userdata 已有内容，跳过迁移；旧数据仍在 $legacyData" -ForegroundColor Yellow
    }
}

# Drop the previous system-drive install, if this is a relocation.
if ((Test-Path $legacyInstall) -and ($legacyInstall -ne $InstallDir) -and ($legacyInstall -ne $PSScriptRoot)) {
    Remove-Tree $legacyInstall
    Write-Host "已移除旧的 C 盘安装目录: $legacyInstall"
}

New-Shortcut $startLnk $exe $InstallDir $icon 'HELLDIVERS 2 // TACTICAL COMMAND'
New-Shortcut $startupLnk $exe $InstallDir $icon 'HELLDIVERS 2 // TACTICAL COMMAND - 开机自启'

Write-Host '已创建：开始菜单项、开机启动项（不建桌面图标）。' -ForegroundColor Green
Write-Host "数据目录: $dataDir"
Write-Host "开始菜单: $startLnk"
Write-Host "启动项:   $startupLnk"
if (-not $NoStart) {
    Start-Process -FilePath $exe -WorkingDirectory $InstallDir
}