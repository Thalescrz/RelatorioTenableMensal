[CmdletBinding()]
param(
    [string]$TaskName = "Relatorios Tenable - Mensal",
    [string]$Time = "00:05",
    [string]$Config = (Join-Path $PSScriptRoot "..\orchestration\clients.json")
)

$ErrorActionPreference = "Stop"
$launcher = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "run_monthly_orchestration.ps1")).Path
$resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -ProjectRoot "{1}" -Config "{2}"' -f $launcher, $projectRoot, $resolvedConfig

& schtasks.exe /Create /TN $TaskName /SC MONTHLY /D 1 /ST $Time /TR $taskCommand /F
exit $LASTEXITCODE
