param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$SourceDrawing,
    [Parameter(Mandatory = $true)][string]$SourceVerificationSidecar,
    [Parameter(Mandatory = $true)][string]$PublicationDirectory,
    [Parameter(Mandatory = $true)][string]$SolidWorksInteropDirectory
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath($RepositoryRoot)
$publication = [IO.Path]::GetFullPath($PublicationDirectory)
if (Test-Path -LiteralPath $publication) {
    if (@(Get-ChildItem -LiteralPath $publication -Force).Count -ne 0) {
        throw "PublicationDirectory must be new or empty: $publication"
    }
} else { New-Item -ItemType Directory -Path $publication | Out-Null }
$compiler = Join-Path $repo 'solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe'
$framework = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$newtonsoft = Join-Path $repo 'solidworks-execution\packages\Newtonsoft.Json.13.0.3\lib\net45\Newtonsoft.Json.dll'
$interop = [IO.Path]::GetFullPath($SolidWorksInteropDirectory)
$references = @(
    (Join-Path $framework 'mscorlib.dll'), (Join-Path $framework 'System.dll'),
    (Join-Path $framework 'System.Core.dll'), $newtonsoft,
    (Join-Path $interop 'SolidWorks.Interop.sldworks.dll'),
    (Join-Path $interop 'SolidWorks.Interop.swconst.dll'),
    (Join-Path $interop 'SolidWorks.Interop.swpublished.dll')
) | ForEach-Object { '/reference:' + $_ }
$source = Join-Path $repo 'solidworks-execution\LayoutTitleBlockFixtureBuilder\Program.cs'
$exe = Join-Path $publication 'LayoutTitleBlockFixtureBuilder.exe'
& $compiler /nologo /nostdlib+ /target:exe /platform:x64 /langversion:latest "/out:$exe" $references $source
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $newtonsoft -Destination $publication
Copy-Item -LiteralPath (Join-Path $interop 'SolidWorks.Interop.sldworks.dll') -Destination $publication
Copy-Item -LiteralPath (Join-Path $interop 'SolidWorks.Interop.swconst.dll') -Destination $publication
Copy-Item -LiteralPath (Join-Path $interop 'SolidWorks.Interop.swpublished.dll') -Destination $publication
& $exe ([IO.Path]::GetFullPath($SourceDrawing)) `
    ([IO.Path]::GetFullPath($SourceVerificationSidecar)) `
    (Join-Path $publication 'g0-title-block-fixture.SLDDRW') `
    (Join-Path $publication 'layout-title-block-fixture.json')
exit $LASTEXITCODE
