param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($RepositoryRoot)
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$validation = [System.IO.Path]::GetFullPath((Join-Path $repo 'validation'))
if ($output -eq $validation -or $output.StartsWith($validation +
    [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must not be validation or one of its descendants.'
}
if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -ne 0) {
        throw "OutputDirectory must be new or empty: $output"
    }
} else { New-Item -ItemType Directory -Path $output | Out-Null }
$compiler = Join-Path $repo 'solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe'
$framework = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$newtonsoft = Join-Path $repo 'solidworks-execution\packages\Newtonsoft.Json.13.0.3\lib\net45\Newtonsoft.Json.dll'
$sources = @(
    'solidworks-execution\DrawingLayoutContractTests\Program.cs',
    'solidworks-execution\SolidworksExecution\Contracts\ViewPlanContractValidator.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DrawingLayoutPlanContractValidator.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DrawingLayoutPlanExecutionCompiler.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DrawingLayoutPlanTransactionPreflight.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DrawingLayoutPlanCapabilityPreflight.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DrawingLayoutPlanQualificationPreflight.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DrawingLayoutPlanVerificationPreflight.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DimensionPlanningHandoffContract.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DimensionPlanContractValidator.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DimensionPlanExecutionCompiler.cs',
    'solidworks-execution\SolidworksExecution\Contracts\DimensionPlanTransactionPreflight.cs'
) | ForEach-Object { Join-Path $repo $_ }
$references = @('mscorlib.dll','System.dll','System.Core.dll') | ForEach-Object {
    '/reference:' + (Join-Path $framework $_)
}
$references += '/reference:' + $newtonsoft
$executable = Join-Path $output 'DrawingLayoutContractTests.exe'
& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest `
    /deterministic+ "/out:$executable" $references $sources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $newtonsoft -Destination $output
& $executable $repo
exit $LASTEXITCODE
