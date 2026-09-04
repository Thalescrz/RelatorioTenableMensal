[CmdletBinding()]
param(
    [string]$Config = (Join-Path $PSScriptRoot "..\orchestration\clients.json"),
    [int]$WaitTimeoutSeconds = 108300
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente nao encontrado. Execute .\scripts\setup.ps1 primeiro."
}
$resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$arguments = @(
    "-m", "tenable_reports", "run-monthly-batch",
    "--project-root", $projectRoot,
    "--config", $resolvedConfig,
    "--wait-timeout-seconds", "$WaitTimeoutSeconds"
)

& $venvPython @arguments
exit $LASTEXITCODE
