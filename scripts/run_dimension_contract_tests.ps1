param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$ProbeRequestDirectory
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($RepositoryRoot)
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
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

$compiler = Join-Path $repo 'solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe'
$framework = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$newtonsoft = Join-Path $repo 'solidworks-execution\packages\Newtonsoft.Json.13.0.3\lib\net45\Newtonsoft.Json.dll'
$sources = @(
    (Join-Path $repo 'solidworks-execution\DimensionContractTests\Program.cs'),
    (Join-Path $repo 'solidworks-execution\SolidworksExecution\Contracts\DimensionApiProbeContract.cs')
)
$required = @($compiler, $newtonsoft, (Join-Path $framework 'mscorlib.dll')) + $sources
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required dimension contract-test dependency is missing: $path"
    }
}
$referencePaths = @(
    (Join-Path $framework 'mscorlib.dll'),
    (Join-Path $framework 'System.dll'),
    (Join-Path $framework 'System.Core.dll'),
    $newtonsoft
)
$references = @($referencePaths | ForEach-Object { '/reference:' + $_ })
$executable = Join-Path $output 'DimensionContractTests.exe'

& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest /deterministic+ "/out:$executable" $references $sources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $newtonsoft -Destination $output
if ($ProbeRequestDirectory) {
    $probeRequests = [System.IO.Path]::GetFullPath($ProbeRequestDirectory)
    if (-not (Test-Path -LiteralPath $probeRequests -PathType Container)) {
        throw "ProbeRequestDirectory is not an existing directory: $probeRequests"
    }
    & $executable $probeRequests
}
else {
    & $executable
}
exit $LASTEXITCODE
