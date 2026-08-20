[CmdletBinding()]
param(
    [string]$TaskName = "Relatorios Tenable - Mensal",
    [string]$Time = "06:00",
    [string]$Config = (Join-Path $PSScriptRoot "..\orchestration\clients.json")
)

$ErrorActionPreference = "Stop"
$launcher = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "run_monthly_orchestration.ps1")).Path
$resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
$taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -Config "{1}"' -f $launcher, $resolvedConfig

& schtasks.exe /Create /TN $TaskName /SC MONTHLY /D 1 /ST $Time /TR $taskCommand /F
exit $LASTEXITCODE
