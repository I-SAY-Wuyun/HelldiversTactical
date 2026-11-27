param(
    [switch]$Console
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$name = if ($Console) { 'HelldiversTacticalDebug' } else { 'HelldiversTactical' }
$windowed = if ($Console) { '--console' } else { '--noconsole' }

python -m PyInstaller --noconfirm --clean --onedir $windowed `
    --name $name `
    --icon 'assets\icon.ico' `
    --runtime-hook 'pyi_rth_hd2_pythonnet.py' `
    --add-data "web;web" `
    --add-data "data;data" `
    --add-data "assets;assets" `
    --collect-all webview --collect-all clr_loader --collect-all pythonnet --collect-all pystray `
    app.py

$out = Join-Path 'dist' $name
Copy-Item 'packaging\HelldiversTactical.exe.config' (Join-Path $out "$name.exe.config") -Force
Copy-Item 'README.md' $out -Force
if (Test-Path 'docs') { Copy-Item 'docs' (Join-Path $out 'docs') -Recurse -Force }
Write-Host "Built: $out"
