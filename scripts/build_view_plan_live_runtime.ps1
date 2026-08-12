param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$SolidWorksInteropDirectory
)

$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($RepositoryRoot)
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$interop = [System.IO.Path]::GetFullPath($SolidWorksInteropDirectory)
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
$packageRoot = Join-Path $repo 'solidworks-execution\packages'
$cosworks = Join-Path $interop 'SolidWorks.Interop.cosworks.dll'
if (-not (Test-Path -LiteralPath $cosworks -PathType Leaf)) {
    $cosworks = Join-Path $interop 'api\redist\SolidWorks.Interop.cosworks.dll'
}
$referencePaths = @(
    (Join-Path $framework 'mscorlib.dll'),
    (Join-Path $framework 'System.dll'),
    (Join-Path $framework 'System.Core.dll'),
    (Join-Path $framework 'System.Drawing.dll'),
    (Join-Path $framework 'System.Net.Http.dll'),
    (Join-Path $framework 'System.Web.dll'),
    (Join-Path $packageRoot 'Microsoft.AspNet.WebApi.Client.5.2.9\lib\net45\System.Net.Http.Formatting.dll'),
    (Join-Path $packageRoot 'Microsoft.AspNet.WebApi.Core.5.2.9\lib\net45\System.Web.Http.dll'),
    (Join-Path $packageRoot 'Microsoft.Owin.4.2.2\lib\net45\Microsoft.Owin.dll'),
    (Join-Path $packageRoot 'Microsoft.Owin.Host.HttpListener.4.2.2\lib\net45\Microsoft.Owin.Host.HttpListener.dll'),
    (Join-Path $packageRoot 'Microsoft.Owin.Hosting.4.2.2\lib\net45\Microsoft.Owin.Hosting.dll'),
    (Join-Path $packageRoot 'Microsoft.AspNet.WebApi.Owin.5.2.9\lib\net45\System.Web.Http.Owin.dll'),
    (Join-Path $packageRoot 'Owin.1.0\lib\net40\Owin.dll'),
    (Join-Path $packageRoot 'Newtonsoft.Json.13.0.3\lib\net45\Newtonsoft.Json.dll'),
    (Join-Path $interop 'SolidWorks.Interop.sldworks.dll'),
    (Join-Path $interop 'SolidWorks.Interop.swconst.dll'),
    (Join-Path $interop 'SolidWorks.Interop.swpublished.dll'),
    $cosworks
)
foreach ($required in @($compiler) + $referencePaths) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required live-runtime dependency is missing: $required"
    }
}
$sources = @(Get-ChildItem -LiteralPath (Join-Path $repo 'solidworks-execution\SolidworksExecution') -Recurse -Filter '*.cs' | ForEach-Object { $_.FullName })
if ($sources.Count -eq 0) {
    throw 'No SolidworksExecution C# sources were found.'
}
$references = @($referencePaths | ForEach-Object { '/reference:' + $_ })
$executable = Join-Path $output 'SolidworksExecution.exe'
& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest /deterministic+ "/out:$executable" $references $sources
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$hostBootstrapDirectory = New-Item -ItemType Directory -Path (Join-Path $output 'HostBootstrap')
$hostBootstrapExecutable = Join-Path $hostBootstrapDirectory 'SolidWorksHostBootstrap.exe'
$hostBootstrapSource = Join-Path $repo 'solidworks-execution\HostBootstrap\Program.cs'
$hostBootstrapReferences = @(
    (Join-Path $framework 'mscorlib.dll'),
    (Join-Path $framework 'System.dll'),
    (Join-Path $framework 'System.Core.dll'),
    (Join-Path $framework 'System.Web.Extensions.dll')
) | ForEach-Object { '/reference:' + $_ }
& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest /deterministic+ "/out:$hostBootstrapExecutable" $hostBootstrapReferences $hostBootstrapSource
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

foreach ($dependency in $referencePaths | Where-Object { $_ -notlike "$framework*" }) {
    Copy-Item -LiteralPath $dependency -Destination $output
}
Copy-Item -LiteralPath (Join-Path $repo 'solidworks-execution\SolidworksExecution\app.config') -Destination ($executable + '.config')
$contracts = New-Item -ItemType Directory -Path (Join-Path $output 'contracts')
Copy-Item -LiteralPath (Join-Path $repo 'drawing_planner\contracts\view-plan.schema.json') -Destination $contracts
Get-FileHash -LiteralPath $executable -Algorithm SHA256 | Select-Object Path, Hash
Get-FileHash -LiteralPath $hostBootstrapExecutable -Algorithm SHA256 | Select-Object Path, Hash
