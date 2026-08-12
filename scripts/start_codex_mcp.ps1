[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$serverEntry = Join-Path $repoRoot "adapters\codex\server.py"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    [Console]::Error.WriteLine("Missing repository Python environment: $pythonExe")
    exit 2
}
if (-not (Test-Path -LiteralPath $serverEntry -PathType Leaf)) {
    [Console]::Error.WriteLine("Missing Codex MCP entry point: $serverEntry")
    exit 2
}

& $pythonExe $serverEntry
exit $LASTEXITCODE
