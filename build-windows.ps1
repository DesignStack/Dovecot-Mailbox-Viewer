# Run in PowerShell on Windows. Outputs a self-contained application folder.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

py -3 -m venv .venv
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
& $python -m unittest discover -s tests -v
& $python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name 'Dovecot Mailbox Viewer' --icon assets\mailbox.ico --paths . viewer\app.py

$output = Join-Path $PSScriptRoot 'Dovecot-Mailbox-Viewer-Windows.zip'
if (Test-Path $output) { Remove-Item $output }
Compress-Archive -Path (Join-Path $PSScriptRoot 'dist\Dovecot Mailbox Viewer') -DestinationPath $output
Write-Host "Windows application ready: $output"
