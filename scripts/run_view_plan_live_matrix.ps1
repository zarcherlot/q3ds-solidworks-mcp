param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$HostPreflightReport,

    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($RepositoryRoot)
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$preflightPath = [System.IO.Path]::GetFullPath($HostPreflightReport)
$python = [System.IO.Path]::GetFullPath($PythonExecutable)
$validation = [System.IO.Path]::GetFullPath((Join-Path $repo 'validation'))
if ($output -eq $validation -or $output.StartsWith($validation + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must not be validation or one of its descendants.'
}
if (Test-Path -LiteralPath $output) {
    if (-not (Test-Path -LiteralPath $output -PathType Container) -or @(Get-ChildItem -LiteralPath $output -Force).Count -ne 0) {
        throw "OutputDirectory must be new or empty: $output"
    }
}
else {
    New-Item -ItemType Directory -Path $output | Out-Null
}
if (-not (Test-Path -LiteralPath $preflightPath -PathType Leaf)) {
    throw "Host preflight report does not exist: $preflightPath"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python executable does not exist: $python"
}
$preflight = Get-Content -Encoding utf8 -Raw -LiteralPath $preflightPath | ConvertFrom-Json
if ($preflight.status -ne 'pass' -or @($preflight.blocking_issues).Count -ne 0 -or @($preflight.warnings).Count -ne 0) {
    throw 'Host preflight must pass without blockers or warnings before the D1 live matrix.'
}
if (-not $preflight.template.provided -or -not $preflight.template.exists) {
    throw 'Host preflight must verify the validation drawing template.'
}
$sldworksInterop = [string]$preflight.solidworks_installation.selected.interop_assemblies[0]
if (-not (Test-Path -LiteralPath $sldworksInterop -PathType Leaf)) {
    throw "Preflight-selected SolidWorks interop does not exist: $sldworksInterop"
}
$interopDirectory = [System.IO.Path]::GetDirectoryName($sldworksInterop)
$runtime = Join-Path $output 'runtime'
$work = Join-Path $output 'work'

& (Join-Path $repo 'scripts\build_view_plan_live_runtime.ps1') -RepositoryRoot $repo -OutputDirectory $runtime -SolidWorksInteropDirectory $interopDirectory
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $python (Join-Path $repo 'scripts\run_view_plan_live_matrix.py') --repository-root $repo --validation-dir $validation --output-dir $work --execution-exe (Join-Path $runtime 'SolidworksExecution.exe')
exit $LASTEXITCODE
