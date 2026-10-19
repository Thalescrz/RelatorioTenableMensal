[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InstallRoot = ""
)

$ErrorActionPreference = "Stop"
$bundleRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$targetRoot = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $bundleRoot
}
else {
    [IO.Path]::GetFullPath($InstallRoot)
}

if ($WhatIfPreference) {
    Write-Output "OFFLINE_SETUP_PLAN platform=windows-x86_64 source=$bundleRoot target=$targetRoot"
    exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "SETUP_REQUIRES_ELEVATION"
}

function Assert-SafeRelativePath([string]$RelativePath) {
    if ([IO.Path]::IsPathRooted($RelativePath) -or $RelativePath -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "BUNDLE_PATH_UNSAFE"
    }
}

function Assert-BundleIntegrity([string]$Root) {
    $manifestPath = Join-Path $Root "bundle-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "BUNDLE_MANIFEST_INVALID"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.platform -ne "windows-x86_64") {
        throw "BUNDLE_PLATFORM_MISMATCH"
    }
    foreach ($entry in $manifest.files) {
        Assert-SafeRelativePath ([string]$entry.path)
        $path = Join-Path $Root ([string]$entry.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "BUNDLE_FILE_MISSING"
        }
        $item = Get-Item -LiteralPath $path
        if ($item.Length -ne [long]$entry.size) {
            throw "BUNDLE_FILE_SIZE_MISMATCH"
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$entry.sha256).ToLowerInvariant()) {
            throw "BUNDLE_FILE_HASH_MISMATCH"
        }
    }
}

Assert-BundleIntegrity $bundleRoot

if ($targetRoot -ne $bundleRoot) {
    New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $bundleRoot -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $targetRoot -Recurse -Force
    }
    Assert-BundleIntegrity $targetRoot
}

$projectRoot = $targetRoot
$stateRoot = Join-Path $projectRoot ".install-state"
$runtimeRoot = Join-Path $projectRoot ".runtime"
$pythonRoot = Join-Path $runtimeRoot "python"
$python = Join-Path $pythonRoot "python.exe"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$wheelhouse = Join-Path $projectRoot "vendor\wheelhouse"
$lockFile = Join-Path $projectRoot "requirements\lock\windows-py314.txt"
$officeRoot = Join-Path $projectRoot "vendor\libreoffice"
$postgresRoot = Join-Path $projectRoot "vendor\postgresql-runtime"
New-Item -ItemType Directory -Force -Path $stateRoot, $runtimeRoot | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $stateRoot "python-3.14.5.ok"))) {
    $installer = Join-Path $projectRoot "vendor\python\python-3.14.5-amd64.exe"
    $arguments = @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_test=0",
        "Include_pip=1", "Include_launcher=0", "TargetDir=`"$pythonRoot`""
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $python)) {
        throw "PYTHON_INSTALL_FAILED"
    }
    New-Item -ItemType File -Force -Path (Join-Path $stateRoot "python-3.14.5.ok") | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $stateRoot "libreoffice-26.2.4.2.ok"))) {
    $installer = Join-Path $projectRoot "vendor\libreoffice\LibreOffice_26.2.4.2_Win_x86-64.msi"
    $arguments = @(
        "/i", "`"$installer`"", "/qn", "/norestart",
        "INSTALLLOCATION=`"$officeRoot`""
    )
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "LIBREOFFICE_INSTALL_FAILED" }
    New-Item -ItemType File -Force -Path (Join-Path $stateRoot "libreoffice-26.2.4.2.ok") | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $stateRoot "postgresql-18.ok"))) {
    $installer = Join-Path $projectRoot "vendor\postgresql\postgresql-18-windows-x64.exe"
    $passwordBytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Fill($passwordBytes)
    $adminPassword = [Convert]::ToBase64String($passwordBytes)
    $optionFile = Join-Path $stateRoot "postgresql-install-options.txt"
    $options = @(
        "mode=unattended", "unattendedmodeui=none", "prefix=$postgresRoot",
        "datadir=$(Join-Path $postgresRoot 'data')", "serverport=5432",
        "superaccount=postgres", "superpassword=$adminPassword",
        "servicename=tenable-reports-postgresql-18"
    )
    [IO.File]::WriteAllLines($optionFile, $options, [Text.UTF8Encoding]::new($false))
    try {
        $process = Start-Process -FilePath $installer -ArgumentList @("--optionfile", $optionFile) -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0) { throw "POSTGRESQL_INSTALL_FAILED" }
        $credentialRoot = Join-Path $projectRoot "credentials"
        New-Item -ItemType Directory -Force -Path $credentialRoot | Out-Null
        & icacls.exe $credentialRoot /inheritance:r /grant:r `
            "$($identity.Name):(OI)(CI)F" `
            "*S-1-5-18:(OI)(CI)F" `
            "*S-1-5-32-544:(OI)(CI)F" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "CREDENTIAL_ACL_FAILED" }
        $adminEnv = Join-Path $credentialRoot "postgresql-admin.env"
        [IO.File]::WriteAllLines($adminEnv, @(
            "TENABLE_REPORTS_ADMIN_HOST=127.0.0.1",
            "TENABLE_REPORTS_ADMIN_PORT=5432",
            "TENABLE_REPORTS_ADMIN_DB=postgres",
            "TENABLE_REPORTS_ADMIN_USER=postgres",
            "TENABLE_REPORTS_ADMIN_PASSWORD=$adminPassword",
            "TENABLE_REPORTS_ADMIN_SSLMODE=prefer"
        ), [Text.UTF8Encoding]::new($false))
    }
    finally {
        Remove-Item -LiteralPath $optionFile -Force -ErrorAction SilentlyContinue
        $adminPassword = $null
    }
    New-Item -ItemType File -Force -Path (Join-Path $stateRoot "postgresql-18.ok") | Out-Null
}

$fontManifest = Join-Path $projectRoot "src\tenable_reports\assets\fonts\font-manifest.json"
$fontSource = Join-Path $projectRoot "vendor\fonts\windows"
$fontTool = Join-Path $projectRoot "tools\windows_font_installation.py"
& $python $fontTool `
    --manifest $fontManifest `
    --system-font-root (Join-Path $env:WINDIR "Fonts") `
    --payload-root $fontSource `
    --apply
if ($LASTEXITCODE -ne 0) { throw "WINDOWS_FONT_REQUIREMENT_UNMET" }
New-Item -ItemType File -Force -Path (Join-Path $stateRoot "fonts-windows-calibri-v1.ok") | Out-Null

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "VENV_CREATE_FAILED" }
}
& $venvPython -m pip install --no-index --find-links $wheelhouse --require-hashes -r $lockFile
if ($LASTEXITCODE -ne 0) { throw "LOCK_INSTALL_FAILED" }
$applicationWheels = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot "vendor\application") -Filter "tenable_reports-*.whl")
if ($applicationWheels.Count -ne 1) { throw "APPLICATION_WHEEL_INVALID" }
$applicationWheel = $applicationWheels[0]
& $venvPython -m pip install --no-index --no-deps --force-reinstall $applicationWheel.FullName
if ($LASTEXITCODE -ne 0) { throw "APPLICATION_INSTALL_FAILED" }

foreach ($directory in @("data", "analysis_artifacts", "outputs", "credentials", "orchestration")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot $directory) | Out-Null
}
foreach ($name in @("clients", "document-control")) {
    $target = Join-Path $projectRoot "orchestration\$name.json"
    if (-not (Test-Path -LiteralPath $target)) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "orchestration\$name.example.json") -Destination $target
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $stateRoot "database-bootstrap.ok"))) {
    $bootstrap = Join-Path $projectRoot "scripts\bootstrap_postgresql.ps1"
    $bootstrapArguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        "`"$bootstrap`"", "-KeepAdminPassword"
    )
    $process = Start-Process -FilePath "powershell.exe" `
        -ArgumentList $bootstrapArguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "DATABASE_BOOTSTRAP_FAILED" }
    $adminEnv = Join-Path $projectRoot "credentials\postgresql-admin.env"
    [IO.File]::WriteAllLines($adminEnv, @(
        "TENABLE_REPORTS_ADMIN_HOST=127.0.0.1",
        "TENABLE_REPORTS_ADMIN_PORT=5432",
        "TENABLE_REPORTS_ADMIN_DB=postgres",
        "TENABLE_REPORTS_ADMIN_USER=postgres",
        "TENABLE_REPORTS_ADMIN_PASSWORD=",
        "TENABLE_REPORTS_ADMIN_SSLMODE=prefer"
    ), [Text.UTF8Encoding]::new($false))
    New-Item -ItemType File -Force -Path (Join-Path $stateRoot "database-bootstrap.ok") | Out-Null
}

& $venvPython -m tenable_reports environment-status --json --require-ready --root $projectRoot --database-env-file (Join-Path $projectRoot "credentials\database.env") --persistent-root (Join-Path $projectRoot "data") --libreoffice (Join-Path $officeRoot "program\soffice.exe")
if ($LASTEXITCODE -ne 0) { throw "ENVIRONMENT_PREFLIGHT_FAILED" }
Write-Output "OFFLINE_SETUP_COMPLETE platform=windows-x86_64 root=$projectRoot"
