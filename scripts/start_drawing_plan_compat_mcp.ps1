[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$serverEntry = Join-Path $repoRoot "adapters\claude\drawing_plan_compat_server.py"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    [Console]::Error.WriteLine("Missing repository Python environment: $pythonExe")
    exit 2
}
if (-not (Test-Path -LiteralPath $serverEntry -PathType Leaf)) {
    [Console]::Error.WriteLine("Missing DrawingPlan 1.0 compatibility entry point: $serverEntry")
    exit 2
}

& $pythonExe $serverEntry
exit $LASTEXITCODE
