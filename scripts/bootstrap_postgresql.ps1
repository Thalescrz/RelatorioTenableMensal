[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5432,
    [string]$DatabaseName = "tenable_reports",
    [string]$ApplicationUser = "tenable_reports_app",
    [string]$AdminUser = "postgres",
    [switch]$KeepAdminPassword
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$credentialDirectory = Join-Path $projectRoot "credentials"
$databaseEnv = Join-Path $credentialDirectory "database.env"
$adminEnv = Join-Path $credentialDirectory "postgresql-admin.env"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Ambiente Python ausente. Execute .\scripts\setup.ps1 primeiro."
}

$adminPassword = $null
$adminPointer = [IntPtr]::Zero
$adminPasswordLoadedFromFile = $false
if (Test-Path -LiteralPath $adminEnv) {
    foreach ($line in Get-Content -LiteralPath $adminEnv) {
        if ($line -match '^\s*TENABLE_REPORTS_ADMIN_PASSWORD\s*=\s*(.+?)\s*$') {
            $adminPassword = $matches[1].Trim('"', "'")
            $adminPasswordLoadedFromFile = -not [string]::IsNullOrWhiteSpace($adminPassword)
            break
        }
    }
}
if (-not $adminPasswordLoadedFromFile) {
    $adminSecure = Read-Host "Senha administrativa do PostgreSQL para $AdminUser" -AsSecureString
    $adminPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminSecure)
    $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($adminPointer)
}
$randomBytes = New-Object byte[] 36
$randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomGenerator.GetBytes($randomBytes)
}
finally {
    $randomGenerator.Dispose()
}
$applicationPassword = [Convert]::ToBase64String($randomBytes)

try {
    New-Item -ItemType Directory -Force -Path $credentialDirectory | Out-Null
    $databaseLines = @(
        "TENABLE_REPORTS_DB_HOST=$HostName",
        "TENABLE_REPORTS_DB_PORT=$Port",
        "TENABLE_REPORTS_DB_NAME=$DatabaseName",
        "TENABLE_REPORTS_DB_USER=$ApplicationUser",
        "TENABLE_REPORTS_DB_PASSWORD=$applicationPassword",
        "TENABLE_REPORTS_DB_SSLMODE=prefer",
        "TENABLE_REPORTS_DB_CONNECT_TIMEOUT=10",
        "TENABLE_REPORTS_DB_APPLICATION_NAME=tenable-reports"
    )
    [IO.File]::WriteAllLines($databaseEnv, $databaseLines, [Text.UTF8Encoding]::new($false))

    $env:TENABLE_REPORTS_ADMIN_HOST = $HostName
    $env:TENABLE_REPORTS_ADMIN_PORT = "$Port"
    $env:TENABLE_REPORTS_ADMIN_DB = "postgres"
    $env:TENABLE_REPORTS_ADMIN_USER = $AdminUser
    $env:TENABLE_REPORTS_ADMIN_PASSWORD = $adminPassword

    Push-Location $projectRoot
    try {
        & $python -m tenable_reports database-bootstrap `
            --database-env-file $databaseEnv `
            --admin-env-file $adminEnv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        & $python -m tenable_reports migrate-legacy-state `
            --database-env-file $databaseEnv `
            --root (Join-Path $projectRoot "data") `
            --root (Join-Path $projectRoot "analysis_artifacts")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        & $python -m tenable_reports database-status `
            --database-env-file $databaseEnv
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($adminPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($adminPointer)
    }
    $adminPassword = $null
    $applicationPassword = $null
    if ($adminPasswordLoadedFromFile -and -not $KeepAdminPassword) {
        $adminLines = @(
            "TENABLE_REPORTS_ADMIN_HOST=$HostName",
            "TENABLE_REPORTS_ADMIN_PORT=$Port",
            "TENABLE_REPORTS_ADMIN_DB=postgres",
            "TENABLE_REPORTS_ADMIN_USER=$AdminUser",
            "TENABLE_REPORTS_ADMIN_PASSWORD=",
            "TENABLE_REPORTS_ADMIN_SSLMODE=prefer"
        )
        [IO.File]::WriteAllLines($adminEnv, $adminLines, [Text.UTF8Encoding]::new($false))
    }
    Remove-Item Env:TENABLE_REPORTS_ADMIN_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:TENABLE_REPORTS_ADMIN_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:TENABLE_REPORTS_ADMIN_DB -ErrorAction SilentlyContinue
    Remove-Item Env:TENABLE_REPORTS_ADMIN_USER -ErrorAction SilentlyContinue
    Remove-Item Env:TENABLE_REPORTS_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
