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
$setupState = Join-Path $repoRoot ".host-setup\repository-host-setup-state.json"
$setupScript = Join-Path $repoRoot "scripts\setup_repository_host.ps1"
$setupLog = Join-Path $repoRoot ".host-setup\mcp-startup-bootstrap.log"
New-Item -ItemType Directory -Path (Split-Path -Parent $setupLog) -Force | Out-Null

if (-not (Test-Path -LiteralPath $setupState -PathType Leaf)) {
    # A missing state identifies the explicit first bootstrap. It may install only the contracted
    # Python/.NET prerequisites; subsequent starts never carry system-install authorization.
    & $setupScript -Mode Configure -AllowSystemPackageInstall *> $setupLog
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            "Repository bootstrap is incomplete. Run scripts\setup_repository_host.ps1 -Mode Configure; " +
            "add -AllowSystemPackageInstall only when Python or .NET Framework installation is explicitly authorized.")
        exit 2
    }
} else {
    # Configure is the idempotence gate. On an unchanged host it validates the fingerprint and
    # immutable output hashes without invoking pip, NuGet or C# compilation.
    & $setupScript -Mode Configure *> $setupLog
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("Repository bootstrap state is stale and automatic repair failed. Review .host-setup\repository-host-setup-report.json.")
        exit 2
    }
}
if (-not (Test-Path -LiteralPath $serverEntry -PathType Leaf)) {
    [Console]::Error.WriteLine("Missing Codex MCP entry point: $serverEntry")
    exit 2
}

& $pythonExe $serverEntry
exit $LASTEXITCODE
