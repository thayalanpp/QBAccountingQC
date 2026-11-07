# Save beside qc_service and tunnel-client in C:\NM\QBAccountingQC.
# Starts existing software; installs nothing and saves no credentials to disk.
# Stop existing QC/tunnel processes before running. Keep this terminal open.
[CmdletBinding()]
param([string]$ProjectRoot = $PSScriptRoot)

$ErrorActionPreference = 'Stop'
$listener = $null
# Exploration uses a separate restricted identity; original named tools keep QC_DB_USER.
$explorerSecret = Read-Host 'SQL password for qb_qc_explorer' -AsSecureString
$env:QC_EXP_DB_USER = 'qb_qc_explorer'
$env:QC_EXP_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $explorerSecret).Password
$explorerSecret = $null
$previousLocation = Get-Location
$envNames = @('QC_DB_SERVER','QC_DB_NAME','QC_DB_USER','QC_DB_PASSWORD',
              'QC_TRUST_CERT','QC_API_TOKEN','QC_PORT','CONTROL_PLANE_API_KEY')
$previousEnv = @{}
foreach ($name in $envNames) {
    $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    Set-Location -LiteralPath $ProjectRoot
    $pythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $tunnelExe = Join-Path $ProjectRoot 'tunnel-client\tunnel-client.exe'
    foreach ($path in @($pythonExe, $tunnelExe,
                        (Join-Path $ProjectRoot 'qc_service\service.py'),
                        (Join-Path $ProjectRoot 'qc_service\explorer_mcp.py'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required file missing: $path. Save this script in your QBAccountingQC folder."
        }
    }

    foreach ($port in @(8765, 8080)) {
        $inUse = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($inUse.Count -gt 0) {
            throw "Port $port is already in use. Close the existing QC listener/tunnel window, then rerun. No existing process was stopped."
        }
    }

    Write-Host 'Enter your SQL password and OpenAI runtime key. Input is hidden.'
    $sqlSecret = Read-Host 'SQL password for qb_qc_reader' -AsSecureString
    $apiSecret = Read-Host 'OpenAI runtime API key (not the QC token)' -AsSecureString
    if ($sqlSecret.Length -eq 0 -or $apiSecret.Length -eq 0) {
        throw 'Both values are required.'
    }
    $env:QC_DB_SERVER = 'NilashaAI'
    $env:QC_DB_NAME = 'QBAccounting'
    $env:QC_DB_USER = 'qb_qc_reader'
    $env:QC_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $sqlSecret).Password
    $env:CONTROL_PLANE_API_KEY = [System.Net.NetworkCredential]::new('', $apiSecret).Password
    $env:QC_TRUST_CERT = 'yes'
    $env:QC_PORT = '8765'
    $generated = & $pythonExe -c 'import secrets; print(secrets.token_urlsafe(32))'
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate QC token.' }
    $env:QC_API_TOKEN = ([string]$generated).Trim()
    if ($env:QC_API_TOKEN.Length -lt 32) { throw 'QC token generation failed.' }

    Write-Host 'Checking SQL connection and an existing named read query...'
    & $pythonExe -c "from qc_service.service import execute; execute('get_account_balance', {'company':'dntl','account':'22200','fy':2025}); print('SQL query succeeded.')"
    if ($LASTEXITCODE -ne 0) {
        throw 'SQL check failed. See the error above. The listener and tunnel were not started.'
    }

    Write-Host 'Checking restricted exploration credentials...'
    & $pythonExe -c "from qc_service.explorer import execute; r=execute('list_tables', {'limit':1}); print('Exploration permission check succeeded.')"
    if ($LASTEXITCODE -ne 0) { throw 'Exploration preflight failed; see error above.' }
    Write-Host 'Starting QC listener in a separate window...'
    $listener = Start-Process -FilePath $pythonExe -ArgumentList '-m','qc_service.explorer_service' -WorkingDirectory $ProjectRoot -PassThru
    $headers = @{ Authorization = 'Bearer ' + $env:QC_API_TOKEN }
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $listener.Refresh()
        if ($listener.HasExited) { throw 'QC listener exited. Check its window for the startup error.' }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Headers $headers -TimeoutSec 2
            if ($health.status -eq 'running') { $ready = $true; break }
        } catch {
            # Allow startup time, but bound retries.
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'QC listener did not become available.' }

    Write-Host 'QC listener is running. Starting tunnel profile qb-qc...'
    Write-Host 'Keep this terminal open. Tunnel status: http://127.0.0.1:8080/ui'
    Write-Host 'Press Ctrl+C to stop the tunnel. This script then stops its QC listener.'
    & $tunnelExe run --profile qb-qc-explorer
    if ($LASTEXITCODE -ne 0) { throw "Tunnel exited with code $LASTEXITCODE. See its output above." }
} finally {
    if ($null -ne $listener) {
        $listener.Refresh()
        if (-not $listener.HasExited) {
            Stop-Process -Id $listener.Id -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in $envNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnv[$name], 'Process')
    }
    $sqlSecret = $null
    $apiSecret = $null
    $headers = $null
    $generated = $null
    Remove-Item Env:QC_EXP_DB_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:QC_EXP_DB_USER -ErrorAction SilentlyContinue
    Set-Location -LiteralPath $previousLocation.Path
}
