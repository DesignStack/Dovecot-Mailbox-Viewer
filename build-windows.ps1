# Run in PowerShell on Windows. Produces one portable EXE and its checksum.
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
$appVersion = & $python scripts\prepare_release.py
if ($LASTEXITCODE -ne 0) { throw "Release metadata validation failed" }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
    --name 'Dovecot-Mailbox-Viewer-Windows' --icon assets\mailbox.ico `
    --version-file build\windows-version.txt --paths . viewer\app.py
if ($LASTEXITCODE -ne 0) { throw "Application build failed" }

$output = Join-Path $PSScriptRoot 'dist\Dovecot-Mailbox-Viewer-Windows.exe'
$fileInfo = (Get-Item $output).VersionInfo
if ($fileInfo.FileVersion -ne $appVersion -or $fileInfo.ProductVersion -ne $appVersion) {
    throw "Executable version metadata does not match $appVersion"
}

# Test the actual bundled application, outside the source tree, without _internal.
$checkDir = Join-Path ([System.IO.Path]::GetTempPath()) ('mail-viewer-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $checkDir | Out-Null
$process = $null
$previousLocalAppData = $env:LOCALAPPDATA
try {
    $testExe = Join-Path $checkDir 'Dovecot-Mailbox-Viewer-Windows.exe'
    Copy-Item $output $testExe
    $env:LOCALAPPDATA = Join-Path $checkDir 'app-data'
    $process = Start-Process -FilePath $testExe -WorkingDirectory $checkDir `
        -ArgumentList '--smoke-test', 'smoke-test.json' -PassThru
    if (-not $process.WaitForExit(60000)) {
        throw "Portable executable check timed out"
    }
    $reportPath = Join-Path $checkDir 'smoke-test.json'
    if ($process.ExitCode -ne 0 -or -not (Test-Path $reportPath)) {
        throw "Portable executable check failed (exit code $($process.ExitCode))"
    }
    $report = Get-Content $reportPath -Raw | ConvertFrom-Json
    if (-not $report.ok -or -not $report.frozen -or $report.version -ne $appVersion) {
        throw "Portable executable report failed: $(Get-Content $reportPath -Raw)"
    }
    Copy-Item $reportPath (Join-Path $PSScriptRoot 'build\smoke-test.json')
    Write-Host "Portable executable check passed for version $appVersion"
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    Remove-Item -Path $checkDir -Recurse -Force
}

$hash = (Get-FileHash -Path $output -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  Dovecot-Mailbox-Viewer-Windows.exe" | Set-Content -Encoding ascii dist\SHA256SUMS.txt
if ($env:GITHUB_OUTPUT) { "version=$appVersion" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8 }
Write-Host "Windows application v$appVersion ready: $output"
