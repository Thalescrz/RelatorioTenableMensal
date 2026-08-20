[CmdletBinding()]
param(
    [string]$Config = (Join-Path $PSScriptRoot "..\orchestration\clients.json"),
    [int]$MaxParallel = 0,
    [switch]$ApplyRetention
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente nao encontrado. Execute .\scripts\setup.ps1 primeiro."
}
$resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$arguments = @(
    "-m", "tenable_reports", "orchestrate",
    "--config", $resolvedConfig,
    "--mode", "automatic",
    "--confirm-live-api"
)
if ($MaxParallel -gt 0) { $arguments += @("--max-parallel", "$MaxParallel") }
if ($ApplyRetention) { $arguments += "--apply-retention" }

& $venvPython @arguments
exit $LASTEXITCODE
