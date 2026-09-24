# Run in PowerShell on Windows. Outputs a self-contained application folder.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

py -3 -m venv .venv
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip update failed" }
& $python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed; build stopped" }
& $python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name 'Dovecot Mailbox Viewer' --icon assets\mailbox.ico --paths . viewer\app.py
if ($LASTEXITCODE -ne 0) { throw "Application build failed" }

$output = Join-Path $PSScriptRoot 'Dovecot-Mailbox-Viewer-Windows.zip'
if (Test-Path $output) { Remove-Item $output }
Compress-Archive -Path (Join-Path $PSScriptRoot 'dist\Dovecot Mailbox Viewer') -DestinationPath $output
Write-Host "Windows application ready: $output"
