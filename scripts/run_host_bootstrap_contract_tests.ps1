[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "solidworks-execution\HostBootstrap\HostBootstrap.csproj"
$source = Join-Path $repoRoot "solidworks-execution\HostBootstrap\Program.cs"
$executable = & (Join-Path $PSScriptRoot "build_host_bootstrap.ps1") -Configuration $Configuration
$executable = @($executable)[-1]

$projectText = Get-Content -LiteralPath $project -Raw
if ($projectText -match "SolidWorks\.Interop|pywin32|makepy") {
    throw "The native helper project must not reference SolidWorks Interop or Python automation."
}
$sourceText = Get-Content -LiteralPath $source -Raw
foreach ($requiredToken in @(
    "RegistryView.Registry64",
    "RegistryView.Registry32",
    "SldWorks.Application",
    "--probe-child",
    "--no-regserver",
    "host-preflight-report.json",
    "Environment.Is64BitProcess",
    "SessionId",
    "SLDWORKS.exe",
    "FileMode.CreateNew",
    "blocking_issues",
    "temporary_files"
)) {
    if (-not $sourceText.Contains($requiredToken)) {
        throw "Native contract token is missing: $requiredToken"
    }
}
if ($sourceText -match "\.codex\\skills|solidworks-host-bootstrap\\native") {
    throw "Repository helper must not contain an external Skill runtime path."
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "solidworks-host-bootstrap-contract-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    & $executable --output-dir $testRoot --skip-solidworks-launch --no-regserver
    $probeExit = $LASTEXITCODE
    if ($probeExit -notin @(0, 2)) {
        throw "Inspection returned unexpected exit code $probeExit."
    }
    $reportPath = Join-Path $testRoot "host-preflight-report.json"
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "Inspection did not publish host-preflight-report.json."
    }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    foreach ($field in @(
        "status", "host", "runtime", "python", "paths", "dependencies", "solidworks",
        "solidworks_installation", "template", "output_dir_check", "elevation",
        "bootstrap_policy", "actions", "temporary_files", "blocking_issues", "warnings",
        "generated_at"
    )) {
        if ($report.PSObject.Properties.Name -notcontains $field) {
            throw "Report field is missing: $field"
        }
    }
    if (-not $report.host.is_64_bit_process) {
        throw "Contract test helper process was not x64."
    }
    if (-not $report.output_dir_check.writable) {
        throw "Contract test output directory probe failed."
    }
    if (($probeExit -eq 0) -ne ($report.status -in @("pass", "warning"))) {
        throw "Inspection exit code and report status disagree."
    }

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $executable --definitely-unknown-option 2>$null
    $malformedExit = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($malformedExit -ne 1) {
        throw "Malformed invocation must exit 1."
    }
} finally {
    $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
    $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (
        $resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTestRoot).StartsWith("solidworks-host-bootstrap-contract-")
    ) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}

Write-Output "HostBootstrap native contract passed."
exit 0
