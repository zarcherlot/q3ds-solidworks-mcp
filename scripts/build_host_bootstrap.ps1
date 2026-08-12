[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "solidworks-execution\HostBootstrap\HostBootstrap.csproj"

$msbuildCommand = Get-Command msbuild.exe -ErrorAction SilentlyContinue
if ($msbuildCommand) {
    $msbuild = $msbuildCommand.Source
} else {
    $msbuild = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe"
}
if (-not (Test-Path -LiteralPath $msbuild -PathType Leaf)) {
    throw "MSBuild was not found. Install a .NET Framework build toolchain."
}

& $msbuild $project /t:Build "/p:Configuration=$Configuration" /v:minimal
if ($LASTEXITCODE -ne 0) {
    throw "HostBootstrap build failed with exit code $LASTEXITCODE."
}

$executable = Join-Path (
    Split-Path -Parent $project
) "bin\$Configuration\SolidWorksHostBootstrap.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Build succeeded without publishing $executable"
}
Write-Output $executable
