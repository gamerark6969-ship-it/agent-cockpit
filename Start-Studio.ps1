[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$backendDir = Join-Path $PSScriptRoot 'backend'
$studioPython = Join-Path $backendDir '.venv\Scripts\python.exe'
$settingsFile = Join-Path $backendDir '.env'

if (-not (Test-Path -LiteralPath $studioPython)) {
    py -3.12 -m venv (Join-Path $backendDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required to create the backend environment.' }
}
& $studioPython -m pip install --disable-pip-version-check -q -r (Join-Path $backendDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }

if (-not (Test-Path -LiteralPath $settingsFile)) {
    # Create a new installation without touching the keys of existing connectors.
    $initialConfig = & $studioPython -c "import secrets; print('APP_TOKEN=' + secrets.token_urlsafe(32)); print('CONNECTOR_SECRET=' + secrets.token_urlsafe(32))"
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate the application credentials.' }
    New-Item -ItemType File -Path $settingsFile -Value ($initialConfig -join [Environment]::NewLine) -ErrorAction Stop | Out-Null
}

Push-Location (Join-Path $PSScriptRoot 'pwa')
try {
    if (-not (Test-Path -LiteralPath 'node_modules/.bin/vite.cmd')) {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    npm.cmd run build -- --mode localstudio
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }

Write-Host "Use APP_TOKEN from $settingsFile to sign in. Keep this window open while jobs run."
& $studioPython (Join-Path $backendDir 'local_studio.py')
exit $LASTEXITCODE
