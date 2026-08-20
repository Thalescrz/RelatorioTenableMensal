[CmdletBinding()]
param(
    [string]$Config = (Join-Path $PSScriptRoot "..\orchestration\clients.json"),
    [int]$Days = 0,
    [string]$StartAt,
    [string]$EndAt,
    [string[]]$Client = @(),
    [int]$MaxParallel = 0
)

$ErrorActionPreference = "Stop"
if (($StartAt -and -not $EndAt) -or ($EndAt -and -not $StartAt)) {
    throw "StartAt e EndAt devem ser informados juntos."
}
if ($Days -gt 0 -and $StartAt) {
    throw "Days nao pode ser combinado com StartAt/EndAt."
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente nao encontrado. Execute .\scripts\setup.ps1 primeiro."
}
$resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$arguments = @(
    "-m", "tenable_reports", "orchestrate",
    "--config", $resolvedConfig,
    "--mode", "manual",
    "--confirm-live-api"
)
if ($Days -gt 0) { $arguments += @("--days", "$Days") }
if ($StartAt) { $arguments += @("--start-at", $StartAt, "--end-at", $EndAt) }
foreach ($clientId in $Client) { $arguments += @("--client", $clientId) }
if ($MaxParallel -gt 0) { $arguments += @("--max-parallel", "$MaxParallel") }

& $venvPython @arguments
exit $LASTEXITCODE
