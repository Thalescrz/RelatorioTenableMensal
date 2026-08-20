[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Config = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedConfig = if ([string]::IsNullOrWhiteSpace($Config)) {
    Join-Path $projectRoot "orchestration\clients.json"
}
else {
    $Config
}
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Ambiente nao encontrado. Execute .\scripts\setup.ps1 primeiro."
}

Push-Location $projectRoot
try {
    $arguments = @(
        "-m", "tenable_reports", "serve-web",
        "--project-root", $projectRoot,
        "--config", $resolvedConfig,
        "--port", "$Port"
    )
    if (-not $NoBrowser) {
        $arguments += "--open-browser"
    }
    & $venvPython @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
