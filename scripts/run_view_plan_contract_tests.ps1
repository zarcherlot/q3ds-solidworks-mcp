param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($RepositoryRoot)
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $repo -PathType Container)) {
    throw "RepositoryRoot is not an existing directory: $repo"
}
$validation = [System.IO.Path]::GetFullPath((Join-Path $repo 'validation'))
if ($output -eq $validation -or $output.StartsWith($validation + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must not be validation or one of its descendants.'
}
if (Test-Path -LiteralPath $output) {
    if (-not (Test-Path -LiteralPath $output -PathType Container)) {
        throw "OutputDirectory is not a directory: $output"
    }
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -ne 0) {
        throw "OutputDirectory must be new or empty: $output"
    }
}
else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

$compiler = Join-Path $repo 'solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe'
$framework = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$newtonsoft = Join-Path $repo 'solidworks-execution\packages\Newtonsoft.Json.13.0.3\lib\net45\Newtonsoft.Json.dll'
$schema = Join-Path $repo 'drawing_planner\contracts\view-plan.schema.json'
foreach ($required in @($compiler, $newtonsoft, $schema, (Join-Path $framework 'mscorlib.dll'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required ViewPlan contract-test dependency is missing: $required"
    }
}

$sourceRelativePaths = @(
    'solidworks-execution\ContractTests\Program.cs',
    'solidworks-execution\ContractTests\SolidWorksServiceStub.cs',
    'solidworks-execution\SolidworksExecution\Contracts\ViewPlanBasicExecutionCompiler.cs',
    'solidworks-execution\SolidworksExecution\Contracts\ViewPlanBasicTransactionPreflight.cs',
    'solidworks-execution\SolidworksExecution\Contracts\ViewPlanBasicVerificationPreflight.cs',
    'solidworks-execution\SolidworksExecution\Contracts\ViewPlanContractValidator.cs',
    'solidworks-execution\SolidworksExecution\Contracts\ViewPlanSectionGeometryResolver.cs',
    'solidworks-execution\SolidworksExecution\Services\SolidWorksService.ViewPlan.cs',
    'solidworks-execution\SolidworksExecution\Infrastructure\IOperationGuard.cs',
    'solidworks-execution\SolidworksExecution\Models\CadState.cs',
    'solidworks-execution\SolidworksExecution\Models\ExecutionResponse.cs',
    'solidworks-execution\SolidworksExecution\Models\ToolRequest.cs'
)
$sources = @($sourceRelativePaths | ForEach-Object {
    $path = Join-Path $repo $_
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required ViewPlan contract-test source is missing: $path"
    }
    $path
})
$referencePaths = @(
    (Join-Path $framework 'mscorlib.dll'),
    (Join-Path $framework 'System.dll'),
    (Join-Path $framework 'System.Core.dll'),
    $newtonsoft
)
$references = @($referencePaths | ForEach-Object { '/reference:' + $_ })
$executable = Join-Path $output 'ViewPlanContractTests.exe'

& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest /deterministic+ "/out:$executable" $references $sources
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Copy-Item -LiteralPath $newtonsoft -Destination $output
$contractDirectory = New-Item -ItemType Directory -Path (Join-Path $output 'contracts')
Copy-Item -LiteralPath $schema -Destination $contractDirectory

& $executable $repo
exit $LASTEXITCODE
