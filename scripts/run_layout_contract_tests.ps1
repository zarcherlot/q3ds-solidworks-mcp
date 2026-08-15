param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($RepositoryRoot)
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$validation = [System.IO.Path]::GetFullPath((Join-Path $repo 'validation'))
if ($output -eq $validation -or $output.StartsWith(
        $validation + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must not be validation or one of its descendants.'
}
if (Test-Path -LiteralPath $output) {
    if (-not (Test-Path -LiteralPath $output -PathType Container) -or
        @(Get-ChildItem -LiteralPath $output -Force).Count -ne 0) {
        throw "OutputDirectory must be new or empty: $output"
    }
}
else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

$compiler = Join-Path $repo 'solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe'
$framework = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$newtonsoft = Join-Path $repo 'solidworks-execution\packages\Newtonsoft.Json.13.0.3\lib\net45\Newtonsoft.Json.dll'
$sources = @(
    (Join-Path $repo 'solidworks-execution\LayoutContractTests\Program.cs'),
    (Join-Path $repo 'solidworks-execution\SolidworksExecution\Contracts\LayoutBoundaryProbeContract.cs'),
    (Join-Path $repo 'solidworks-execution\SolidworksExecution\Contracts\LayoutPlanningHandoffContract.cs'),
    (Join-Path $repo 'solidworks-execution\SolidworksExecution\Contracts\DimensionPlanningHandoffContract.cs')
    (Join-Path $repo 'solidworks-execution\SolidworksExecution\Services\LayoutDisplayGeometry.cs')
)
$required = @($compiler, $newtonsoft, (Join-Path $framework 'mscorlib.dll')) + $sources
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required layout contract-test dependency is missing: $path"
    }
}
$referencePaths = @(
    (Join-Path $framework 'mscorlib.dll'),
    (Join-Path $framework 'System.dll'),
    (Join-Path $framework 'System.Core.dll'),
    $newtonsoft
)
$references = @($referencePaths | ForEach-Object { '/reference:' + $_ })
$executable = Join-Path $output 'LayoutContractTests.exe'
& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest `
    /deterministic+ "/out:$executable" $references $sources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $newtonsoft -Destination $output
& $executable
exit $LASTEXITCODE
