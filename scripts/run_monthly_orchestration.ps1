[CmdletBinding()]
param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot ".."),
    [string]$Config = "orchestration\clients.json",
    [int]$WaitTimeoutSeconds = 108300,
    [string]$PythonExecutable = "",
    [string]$LibreOfficePath = "",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Set-Location -LiteralPath $projectRoot
$venvPython = if ($PythonExecutable) {
    (Resolve-Path -LiteralPath $PythonExecutable).Path
} else {
    Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente nao encontrado. Execute .\scripts\setup.ps1 primeiro."
}
$configPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $projectRoot $Config }
$resolvedConfig = (Resolve-Path -LiteralPath $configPath).Path

$officeCandidates = @()
if ($LibreOfficePath) { $officeCandidates += $LibreOfficePath }
$officeCandidates += (Join-Path $projectRoot "vendor\libreoffice\program\soffice.exe")
$officeCandidates += (Join-Path (Split-Path -Parent $projectRoot) "LibreOfficePortable\App\libreoffice\program\soffice.exe")
foreach ($variable in @("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")) {
    $root = [Environment]::GetEnvironmentVariable($variable)
    if ($root) { $officeCandidates += (Join-Path $root "LibreOffice\program\soffice.exe") }
}
$resolvedLibreOffice = $officeCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -First 1
if (-not $resolvedLibreOffice) {
    throw "LibreOffice homologado nao localizado para a automacao mensal."
}
$resolvedLibreOffice = (Resolve-Path -LiteralPath $resolvedLibreOffice).Path

if ($PreflightOnly) {
    $preflightArguments = @(
        "-m", "tenable_reports", "environment-status",
        "--root", $projectRoot,
        "--json", "--require-ready",
        "--libreoffice", $resolvedLibreOffice
    )
    & $venvPython @preflightArguments
    exit $LASTEXITCODE
}

$arguments = @(
    "-m", "tenable_reports", "run-monthly-batch",
    "--project-root", $projectRoot,
    "--config", $resolvedConfig,
    "--wait-timeout-seconds", "$WaitTimeoutSeconds",
    "--libreoffice", $resolvedLibreOffice
)

$startedAt = [DateTimeOffset]::UtcNow
$captured = @(& $venvPython @arguments 2>&1)
$executionExitCode = $LASTEXITCODE
$finishedAt = [DateTimeOffset]::UtcNow
$stdoutLines = @()
$stderrLines = @()
foreach ($item in $captured) {
    if ($item -is [System.Management.Automation.ErrorRecord]) {
        $stderrLines += $item.ToString()
        [Console]::Error.WriteLine($item.ToString())
    } else {
        $stdoutLines += [string]$item
        Write-Output ([string]$item)
    }
}

$logPayload = @{
    started_at = $startedAt.ToString("o")
    finished_at = $finishedAt.ToString("o")
    exit_code = $executionExitCode
    stdout = $stdoutLines
    stderr = $stderrLines
} | ConvertTo-Json -Depth 8 -Compress
$logPath = Join-Path $projectRoot "data\automation-logs\monthly-scheduler.jsonl"
$logWriter = Join-Path $projectRoot "tools\write_monthly_automation_log.py"
$logPayload | & $venvPython $logWriter --log-path $logPath
if ($LASTEXITCODE -ne 0 -and $executionExitCode -eq 0) {
    exit 2
}

exit $executionExitCode
