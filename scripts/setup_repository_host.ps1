[CmdletBinding()]
param(
    [ValidateSet("Inspect", "Configure", "Verify")]
    [string]$Mode = "Inspect",

    [ValidateSet("Runtime", "Development")]
    [string]$DependencySet = "Runtime",

    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug",

    [string]$ReportDirectory,
    [string]$PythonExecutable,
    [string]$SolidWorksInteropDirectory,
    [string]$SolidWorksApiRedistDirectory,

    [switch]$AllowSystemPackageInstall
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$script:RepoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if ([string]::IsNullOrWhiteSpace($ReportDirectory)) {
    $ReportDirectory = Join-Path $script:RepoRoot ".host-setup"
}
$script:ReportRoot = [System.IO.Path]::GetFullPath($ReportDirectory)
$script:Checks = [System.Collections.ArrayList]::new()
$script:Actions = [System.Collections.ArrayList]::new()
$script:Blockers = [System.Collections.ArrayList]::new()
$script:Warnings = [System.Collections.ArrayList]::new()
$script:Outputs = [ordered]@{}
$script:Python = $null
$script:NuGet = $null
$script:Interop = $null
$script:ApiRedist = $null
$script:NuGetVersion = "6.11.1"
$script:NuGetSha256 = "c0ddc9cb0633c4607da7e8028eb4f91248c8b74e45a68b0c79fcfa7d78c2a481"
$script:SetupStatePath = Join-Path $script:RepoRoot ".host-setup\repository-host-setup-state.json"
$script:SetupFingerprint = $null
$script:PublishSetupState = $false

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Message, [object]$Details = $null)
    [void]$script:Checks.Add([ordered]@{
        name = $Name
        status = $Status
        message = $Message
        details = $Details
    })
}

function Add-Action {
    param([string]$Name, [string]$Status, [string]$Message, [object]$Details = $null)
    [void]$script:Actions.Add([ordered]@{
        name = $Name
        status = $Status
        message = $Message
        details = $Details
    })
}

function Add-Blocker {
    param([string]$Code, [string]$Message, [string]$Remediation)
    [void]$script:Blockers.Add([ordered]@{
        code = $Code
        message = $Message
        remediation = $Remediation
    })
}

function Add-Warning {
    param([string]$Code, [string]$Message)
    [void]$script:Warnings.Add([ordered]@{ code = $Code; message = $Message })
}

function Get-FileSha256 {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-SetupInputFingerprint {
    param([object]$Python, [string]$InteropDirectory, [string]$ApiRedistDirectory)
    $lockFile = if ($DependencySet -eq "Development") { "requirements-dev.lock" } else { "requirements.lock" }
    $relativeFiles = @(
        $lockFile,
        "scripts\setup_repository_host.ps1",
        "scripts\build_view_plan_live_runtime.ps1",
        "solidworks-execution\SolidworksExecution\packages.config",
        "solidworks-execution\SolidworksExecution\app.config",
        "drawing_planner\contracts\view-plan.schema.json",
        "drawing_planner\taxonomies\mechanical-features-1.0.0-experimental.json"
    )
    $sourceRoot = Join-Path $script:RepoRoot "solidworks-execution\SolidworksExecution"
    $relativeFiles += @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -Filter "*.cs" |
        ForEach-Object { $_.FullName.Substring($script:RepoRoot.Length + 1) })
    $relativeFiles += "solidworks-execution\HostBootstrap\Program.cs"
    $repositoryInputs = foreach ($relative in $relativeFiles | Sort-Object -Unique) {
        $path = Join-Path $script:RepoRoot $relative
        [ordered]@{ path = $relative; sha256 = Get-FileSha256 $path }
    }
    $externalInputs = foreach ($path in @(
        $Python.executable,
        (Join-Path $InteropDirectory "SolidWorks.Interop.sldworks.dll"),
        (Join-Path $InteropDirectory "SolidWorks.Interop.swconst.dll"),
        (Join-Path $InteropDirectory "SolidWorks.Interop.swpublished.dll"),
        (Join-Path $ApiRedistDirectory "SolidWorks.Interop.cosworks.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\mscorlib.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.Core.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.Drawing.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.Net.Http.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.Web.dll"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.Web.Extensions.dll")
    )) {
        [ordered]@{ path = $path; sha256 = Get-FileSha256 $path }
    }
    $payload = [ordered]@{
        schema_version = 1
        machine_name = [Environment]::MachineName
        os_version = [Environment]::OSVersion.VersionString
        configuration = $Configuration
        dependency_set = $DependencySet
        python = [ordered]@{
            executable = $Python.executable
            version = $Python.version
            architecture = $Python.architecture
        }
        repository_inputs = @($repositoryInputs)
        external_inputs = @($externalInputs)
    }
    $json = $payload | ConvertTo-Json -Depth 10 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = $algorithm.ComputeHash($bytes) } finally { $algorithm.Dispose() }
    return (($hash | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Read-SetupState {
    if (-not (Test-Path -LiteralPath $script:SetupStatePath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $script:SetupStatePath -Raw | ConvertFrom-Json } catch { return $null }
}

function Test-SetupState {
    param([string]$Fingerprint)
    $state = Read-SetupState
    if ($null -eq $state -or $state.schema_version -ne 1 -or $state.fingerprint -ne $Fingerprint) { return $false }
    if (@($state.outputs).Count -eq 0) { return $false }
    foreach ($output in @($state.outputs)) {
        if (-not (Test-Path -LiteralPath $output.path -PathType Leaf) -or
            (Get-FileSha256 $output.path) -ne $output.sha256) { return $false }
    }
    $probe = & $script:Python.executable -c "import fastmcp,httpx,jsonschema,dotenv" 2>$null
    return $LASTEXITCODE -eq 0
}

function Write-SetupState {
    param([string]$Fingerprint)
    $deploymentRoot = Join-Path $script:RepoRoot "solidworks-execution\SolidworksExecution\bin\$Configuration"
    $outputPaths = @(Get-ChildItem -LiteralPath $deploymentRoot -Recurse -File |
        Where-Object { $_.Extension -notin @(".log", ".tmp") } |
        ForEach-Object { $_.FullName })
    $outputPaths += Join-Path $script:RepoRoot "solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe"
    $outputPaths += Join-Path $script:RepoRoot (".host-setup\tools\nuget-" + $script:NuGetVersion + ".exe")
    $outputPaths = @($outputPaths | Sort-Object -Unique)
    $state = [ordered]@{
        schema_version = 1
        fingerprint = $Fingerprint
        generated_at = [DateTime]::UtcNow.ToString("o")
        outputs = @($outputPaths | ForEach-Object { [ordered]@{ path = $_; sha256 = Get-FileSha256 $_ } })
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $script:SetupStatePath) -Force | Out-Null
    $temporary = $script:SetupStatePath + "." + [guid]::NewGuid().ToString("N") + ".tmp"
    [System.IO.File]::WriteAllText($temporary, ($state | ConvertTo-Json -Depth 6) + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Publish-AtomicFile $temporary $script:SetupStatePath
    $script:Outputs["setup_state"] = $script:SetupStatePath
    $script:Outputs["setup_fingerprint"] = $Fingerprint
}

function Get-MissingFrameworkReferences {
    $framework = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
    return @("mscorlib.dll", "System.dll", "System.Core.dll", "System.Drawing.dll", "System.Net.Http.dll", "System.Web.dll", "System.Web.Extensions.dll" |
        ForEach-Object { Join-Path $framework $_ } |
        Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
}

function Publish-AtomicFile {
    param([string]$TemporaryPath, [string]$TargetPath)
    if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
        $backup = $TargetPath + "." + [guid]::NewGuid().ToString("N") + ".backup"
        try {
            [System.IO.File]::Replace($TemporaryPath, $TargetPath, $backup)
        } finally {
            if (Test-Path -LiteralPath $backup -PathType Leaf) {
                Remove-Item -LiteralPath $backup -Force
            }
        }
    } else {
        [System.IO.File]::Move($TemporaryPath, $TargetPath)
    }
}

function Write-SetupReport {
    param([string]$UnhandledError)
    if (-not [string]::IsNullOrWhiteSpace($UnhandledError)) {
        Add-Blocker "SETUP_UNHANDLED_ERROR" $UnhandledError "Review the failed action and rerun the same explicit mode after correcting it."
    }
    if (-not (Test-Path -LiteralPath $script:ReportRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $script:ReportRoot -Force | Out-Null
    }
    $status = if ($script:Blockers.Count -gt 0) {
        "blocked"
    } elseif ($script:Warnings.Count -gt 0) {
        "warning"
    } else {
        "pass"
    }
    $target = Join-Path $script:ReportRoot "repository-host-setup-report.json"
    $script:Outputs["report_path"] = $target
    $report = [ordered]@{
        kind = "solidpilot_repository_host_setup"
        schema_version = 1
        generated_at = [DateTime]::UtcNow.ToString("o")
        mode = $Mode.ToLowerInvariant()
        status = $status
        repository_root = $script:RepoRoot
        policy = [ordered]@{
            runs_before_semantic_mcp = $true
            repository_scoped_changes_only_by_default = $true
            system_package_install_authorized = [bool]$AllowSystemPackageInstall
            installs_solidworks = $false
            manages_solidworks_license = $false
            elevates_process = $false
            launches_solidworks = $false
        }
        checks = @($script:Checks)
        actions = @($script:Actions)
        blocking_issues = @($script:Blockers)
        warnings = @($script:Warnings)
        outputs = $script:Outputs
    }
    $temporary = Join-Path $script:ReportRoot (".repository-host-setup-report." + [guid]::NewGuid().ToString("N") + ".tmp")
    $json = $report | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Publish-AtomicFile $temporary $target
    $finalHash = Get-FileSha256 $target
    $script:FinalStatus = $status
    Write-Output ($target + "`nsha256=" + $finalHash + "`nstatus=" + $status)
}

function Test-PythonInvocation {
    param([string]$Command, [string[]]$PrefixArguments = @())
    try {
        $probe = & $Command @PrefixArguments -c "import json,platform,sys,sysconfig; print(json.dumps({'executable':sys.executable,'version':platform.python_version(),'major':sys.version_info.major,'minor':sys.version_info.minor,'bits':platform.architecture()[0],'runtime_platform':sysconfig.get_platform()}))" 2>$null
        if ($LASTEXITCODE -ne 0 -or @($probe).Count -eq 0) { return $null }
        $value = (@($probe)[-1] | ConvertFrom-Json)
        if ($value.major -ne 3 -or $value.minor -ne 12 -or $value.bits -ne "64bit" -or
            [string]$value.runtime_platform -notmatch '^win-') { return $null }
        return [ordered]@{
            command = $Command
            prefix_arguments = @($PrefixArguments)
            executable = [System.IO.Path]::GetFullPath([string]$value.executable)
            version = [string]$value.version
            architecture = [string]$value.bits
            runtime_platform = [string]$value.runtime_platform
        }
    } catch {
        return $null
    }
}

function Find-BasePython {
    if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
        $candidate = [System.IO.Path]::GetFullPath($PythonExecutable)
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return Test-PythonInvocation $candidate
        }
        return $null
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $value = Test-PythonInvocation $launcher.Source @("-3.12")
        if ($null -ne $value) { return $value }
    }
    foreach ($name in @("python.exe", "python3.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) {
            $value = Test-PythonInvocation $command.Source
            if ($null -ne $value) { return $value }
        }
    }
    foreach ($path in @(
        (Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )) {
        if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $value = Test-PythonInvocation $path
            if ($null -ne $value) { return $value }
        }
    }
    return $null
}

function Find-VenvPython {
    $path = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    return Test-PythonInvocation $path
}

function Ensure-RoslynCompiler {
    param([string]$NuGetPath)
    $compiler = Join-Path $script:RepoRoot "solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe"
    if (Test-Path -LiteralPath $compiler -PathType Leaf) { return $compiler }
    $packageRoot = Join-Path $script:RepoRoot "solidworks-execution\packages"
    & $NuGetPath install Microsoft.Net.Compilers.Toolset -Version 4.14.0 -OutputDirectory $packageRoot -ExcludeVersion -NonInteractive -Verbosity quiet
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
        throw "The pinned Roslyn compiler package could not be restored."
    }
    Add-Action "restore_roslyn_compiler" "completed" "Restored Microsoft.Net.Compilers.Toolset 4.14.0 for repository-local x64 compilation." @{ path = $compiler }
    return $compiler
}

function Find-NuGet {
    $managed = Join-Path $script:RepoRoot (".host-setup\tools\nuget-" + $script:NuGetVersion + ".exe")
    if ((Get-FileSha256 $managed) -eq $script:NuGetSha256) { return $managed }
    $command = Get-Command nuget.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Find-InteropDirectory {
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($value in @($SolidWorksInteropDirectory, $env:SOLIDWORKS_INTEROP_DIR,
        "C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS",
        "C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist")) {
        if (-not [string]::IsNullOrWhiteSpace($value)) { $candidates.Add($value) }
    }
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        try {
            $root = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::ClassesRoot, $view)
            $clsidKey = $root.OpenSubKey("SldWorks.Application\CLSID")
            $clsid = if ($clsidKey) { [string]$clsidKey.GetValue($null) } else { $null }
            if ($clsidKey) { $clsidKey.Dispose() }
            if (-not [string]::IsNullOrWhiteSpace($clsid)) {
                $serverKey = $root.OpenSubKey("CLSID\$clsid\LocalServer32")
                $server = if ($serverKey) { [string]$serverKey.GetValue($null) } else { $null }
                if ($serverKey) { $serverKey.Dispose() }
                if (-not [string]::IsNullOrWhiteSpace($server)) {
                    $match = [regex]::Match($server, '(?i)(?:^"(?<p>[^"]+\.exe)"|^(?<p>.+?\.exe))')
                    if ($match.Success) { $candidates.Add((Split-Path -Parent $match.Groups["p"].Value)) }
                }
            }
            $root.Dispose()
        } catch { }
    }
    foreach ($candidate in $candidates) {
        try { $full = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($candidate)) } catch { continue }
        if ((Test-Path -LiteralPath (Join-Path $full "SolidWorks.Interop.sldworks.dll") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $full "SolidWorks.Interop.swconst.dll") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $full "SolidWorks.Interop.swpublished.dll") -PathType Leaf)) {
            return $full
        }
    }
    return $null
}

function Find-ApiRedistDirectory {
    param([string]$InteropDirectory)
    foreach ($candidate in @($SolidWorksApiRedistDirectory, $env:SOLIDWORKS_API_REDIST_DIR,
        $InteropDirectory, (Join-Path $InteropDirectory "api\redist"))) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try { $full = [System.IO.Path]::GetFullPath($candidate) } catch { continue }
        if (Test-Path -LiteralPath (Join-Path $full "SolidWorks.Interop.cosworks.dll") -PathType Leaf) { return $full }
    }
    return $null
}

function Install-SystemPackage {
    param([string]$Id, [string[]]$OverrideArguments = @())
    if (-not $AllowSystemPackageInstall) { return $false }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Add-Blocker "WINGET_MISSING" "winget is unavailable, so the explicitly authorized system prerequisite cannot be installed." "Install Microsoft App Installer or install $Id manually."
        return $false
    }
    $arguments = @("install", "--id", $Id, "--exact", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity") + $OverrideArguments
    & $winget.Source @arguments
    if ($LASTEXITCODE -ne 0) {
        Add-Blocker "SYSTEM_PACKAGE_INSTALL_FAILED" "winget failed to install $Id with exit code $LASTEXITCODE." "Review the winget output, install the prerequisite manually, and rerun Configure."
        return $false
    }
    Add-Action "install_system_package" "completed" "Installed the explicitly authorized prerequisite $Id." @{ package_id = $Id }
    return $true
}

function Ensure-NuGet {
    $toolDirectory = Join-Path $script:RepoRoot ".host-setup\tools"
    New-Item -ItemType Directory -Path $toolDirectory -Force | Out-Null
    $target = Join-Path $toolDirectory ("nuget-" + $script:NuGetVersion + ".exe")
    $existingHash = Get-FileSha256 $target
    if ($existingHash -eq $script:NuGetSha256) { return $target }
    if ($null -ne $existingHash) {
        Add-Warning "MANAGED_NUGET_HASH_MISMATCH" "The existing managed NuGet CLI did not match the pinned SHA-256 and will be replaced."
    }
    $temporary = $target + "." + [guid]::NewGuid().ToString("N") + ".download"
    try {
        $url = "https://dist.nuget.org/win-x86-commandline/v$($script:NuGetVersion)/nuget.exe"
        Invoke-WebRequest -UseBasicParsing $url -OutFile $temporary
        $downloadHash = Get-FileSha256 $temporary
        if ($downloadHash -ne $script:NuGetSha256) {
            throw "Downloaded NuGet CLI did not match the pinned SHA-256."
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $temporary
        if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
            $null -eq $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
            throw "Downloaded NuGet CLI did not have a valid Microsoft Authenticode signature."
        }
        Publish-AtomicFile $temporary $target
        Add-Action "acquire_nuget" "completed" "Downloaded the pinned NuGet CLI and verified its Microsoft Authenticode signature." @{
            version = "6.11.1"
            url = $url
            sha256 = $downloadHash
        }
        return $target
    } finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Invoke-Configure {
    $venvPath = Join-Path $script:RepoRoot ".venv"
    $venvPython = Find-VenvPython
    if ($null -eq $venvPython) {
        if (Test-Path -LiteralPath $venvPath) {
            Add-Blocker "VENV_INCOMPLETE" "The existing repository .venv is incomplete or is not Python 3.12 x64." "Move the existing .venv aside after preserving anything needed, then rerun Configure."
            return
        }
        $basePython = Find-BasePython
        if ($null -eq $basePython -and $AllowSystemPackageInstall) {
            [void](Install-SystemPackage "Python.Python.3.12")
            $basePython = Find-BasePython
        }
        if ($null -eq $basePython) {
            Add-Blocker "PYTHON_312_X64_MISSING" "Python 3.12 x64 was not found." "Install Python 3.12 x64, pass -PythonExecutable, or explicitly authorize winget with -AllowSystemPackageInstall."
            return
        }
        & $basePython.command @($basePython.prefix_arguments) -m venv $venvPath
        if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed with exit code $LASTEXITCODE." }
        Add-Action "create_virtual_environment" "completed" "Created the repository Python 3.12 virtual environment." @{ path = $venvPath }
        $venvPython = Find-VenvPython
    } else {
        Add-Action "create_virtual_environment" "not_needed" "The repository Python 3.12 virtual environment already exists." @{ path = $venvPath }
    }
    if ($null -eq $venvPython) { throw "The repository virtual environment was not usable after creation." }
    $script:Python = $venvPython
    & $venvPython.executable -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
    if ($LASTEXITCODE -ne 0) {
        & $venvPython.executable -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw "Python ensurepip failed with exit code $LASTEXITCODE." }
        Add-Action "bootstrap_pip" "completed" "Bootstrapped pip inside the repository virtual environment using Python ensurepip."
    } else {
        Add-Action "bootstrap_pip" "not_needed" "pip is already available inside the repository virtual environment."
    }
    $environmentPath = Join-Path $script:RepoRoot "adapters\claude\.env"
    if (-not (Test-Path -LiteralPath $environmentPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $script:RepoRoot "adapters\claude\.env.example") -Destination $environmentPath
        Add-Action "create_local_environment_file" "completed" "Created the ignored local .env from the public defaults." @{ path = $environmentPath }
    } else {
        Add-Action "create_local_environment_file" "not_needed" "The local .env already exists and was not overwritten." @{ path = $environmentPath }
    }
    $script:Interop = Find-InteropDirectory
    if ($null -eq $script:Interop) {
        Add-Blocker "SOLIDWORKS_INTEROP_MISSING" "SolidWorks Interop assemblies were not found, so the execution service cannot be built." "Install SolidWorks outside this script or pass -SolidWorksInteropDirectory."
        return
    }
    $script:ApiRedist = Find-ApiRedistDirectory $script:Interop
    if ($null -eq $script:ApiRedist) {
        Add-Blocker "SOLIDWORKS_COSWORKS_INTEROP_MISSING" "SolidWorks.Interop.cosworks.dll was not found." "Pass -SolidWorksApiRedistDirectory or repair the external SolidWorks installation."
        return
    }
    $missingFramework = @(Get-MissingFrameworkReferences)
    if ($missingFramework.Count -gt 0 -and $AllowSystemPackageInstall) {
        [void](Install-SystemPackage "Microsoft.DotNet.Framework.DeveloperPack_4")
        $missingFramework = @(Get-MissingFrameworkReferences)
    }
    if ($missingFramework.Count -gt 0) {
        Add-Blocker "DOTNET_FRAMEWORK_RUNTIME_MISSING" "Required .NET Framework x64 runtime references were not found." "Install the .NET Framework 4.8.1 Developer Pack, or explicitly authorize winget with -AllowSystemPackageInstall. Visual Studio is not required."
        return
    }
    $script:SetupFingerprint = Get-SetupInputFingerprint $venvPython $script:Interop $script:ApiRedist
    $script:Outputs["setup_fingerprint"] = $script:SetupFingerprint
    if (Test-SetupState $script:SetupFingerprint) {
        $script:Outputs["setup_state"] = $script:SetupStatePath
        Add-Action "configure_repository_runtime" "not_needed" "The host and repository inputs match the completed bootstrap state; dependency restore and compilation were skipped." @{
            state_path = $script:SetupStatePath
            fingerprint = $script:SetupFingerprint
        }
        return
    }
    $lockName = if ($DependencySet -eq "Development") { "requirements-dev.lock" } else { "requirements.lock" }
    $lockPath = Join-Path $script:RepoRoot $lockName
    & $venvPython.executable -m pip install --quiet --disable-pip-version-check --require-hashes -r $lockPath
    if ($LASTEXITCODE -ne 0) { throw "Hash-locked Python dependency installation failed with exit code $LASTEXITCODE." }
    Add-Action "install_python_dependencies" "completed" "Installed the hash-locked $DependencySet dependency set." @{
        lock_path = $lockPath
        lock_sha256 = Get-FileSha256 $lockPath
    }
    $script:NuGet = Ensure-NuGet
    $packagesConfig = Join-Path $script:RepoRoot "solidworks-execution\SolidworksExecution\packages.config"
    $packagesDirectory = Join-Path $script:RepoRoot "solidworks-execution\packages"
    & $script:NuGet install $packagesConfig -OutputDirectory $packagesDirectory -NonInteractive -Verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "NuGet package restore failed with exit code $LASTEXITCODE." }
    Add-Action "restore_csharp_packages" "completed" "Restored the execution-service packages.config dependencies." @{ path = $packagesDirectory }
    [void](Ensure-RoslynCompiler $script:NuGet)
    $output = Join-Path $script:RepoRoot (".host-setup\runtime-build-" + [guid]::NewGuid().ToString("N"))
    try {
        $buildScript = Join-Path $script:RepoRoot "scripts\build_view_plan_live_runtime.ps1"
        $buildArguments = @{
            RepositoryRoot = $script:RepoRoot
            OutputDirectory = $output
            SolidWorksInteropDirectory = $script:Interop
        }
        if ((Get-Command $buildScript).Parameters.ContainsKey("SolidWorksApiRedistDirectory")) {
            $buildArguments["SolidWorksApiRedistDirectory"] = $script:ApiRedist
        } elseif (-not $script:ApiRedist.StartsWith($script:Interop, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The repository build script cannot accept a separate SolidWorks API redist directory."
        }
        & $buildScript @buildArguments | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Repository-local Roslyn execution-service build failed with exit code $LASTEXITCODE." }
        $finalOutput = Join-Path $script:RepoRoot "solidworks-execution\SolidworksExecution\bin\$Configuration"
        New-Item -ItemType Directory -Path $finalOutput -Force | Out-Null
        Copy-Item -Path (Join-Path $output "*") -Destination $finalOutput -Recurse -Force
    } finally {
        if (Test-Path -LiteralPath $output -PathType Container) { Remove-Item -LiteralPath $output -Recurse -Force }
    }
    Add-Action "build_execution_service" "completed" "Built the x64 C# Execution Service and native HostBootstrap helper with the pinned repository-local Roslyn compiler." @{
        configuration = $Configuration
        builder = "Microsoft.Net.Compilers.Toolset/4.14.0"
        output = $finalOutput
    }
    $script:PublishSetupState = $true
}

function Collect-ReadinessChecks {
    if (-not [Environment]::Is64BitOperatingSystem) {
        Add-Check "windows_x64" "fail" "A 64-bit Windows host is required."
        Add-Blocker "WINDOWS_X64_REQUIRED" "The host is not 64-bit Windows." "Use a supported 64-bit Windows host."
    } else {
        Add-Check "windows_x64" "pass" "The host operating system is 64-bit Windows."
    }
    foreach ($relative in @("requirements.lock", "requirements-dev.lock", ".codex\config.toml", "scripts\start_codex_mcp.ps1", "adapters\codex\server.py")) {
        $path = Join-Path $script:RepoRoot $relative
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Add-Check ("repository_file:" + $relative) "pass" "Required repository file is present." @{ path = $path; sha256 = Get-FileSha256 $path }
        } else {
            Add-Check ("repository_file:" + $relative) "fail" "Required repository file is missing." @{ path = $path }
            Add-Blocker "REPOSITORY_FILE_MISSING" "Required repository file is missing: $relative" "Restore the repository checkout before configuring the host."
        }
    }
    $script:Python = Find-VenvPython
    if ($null -eq $script:Python) {
        Add-Check "repository_python" "fail" "The repository .venv is missing or is not Python 3.12 x64."
        Add-Blocker "REPOSITORY_PYTHON_NOT_READY" "The MCP launcher cannot use the required repository Python environment." "Run Configure with a Python 3.12 x64 runtime available."
    } else {
        Add-Check "repository_python" "pass" "The repository .venv uses Python 3.12 x64." $script:Python
        $script:Outputs["python_executable"] = $script:Python.executable
    }
    $roslyn = Join-Path $script:RepoRoot "solidworks-execution\packages\Microsoft.Net.Compilers.Toolset\tasks\net472\csc.exe"
    if (Test-Path -LiteralPath $roslyn -PathType Leaf) {
        Add-Check "csharp_builder" "pass" "The repository-local Roslyn x64 compiler is available." @{ path = $roslyn; sha256 = Get-FileSha256 $roslyn }
    } else {
        Add-Check "csharp_builder" "fail" "The pinned repository-local Roslyn compiler is unavailable."
        Add-Blocker "CSHARP_BUILDER_NOT_READY" "The C# execution service cannot be rebuilt." "Run Configure to restore the pinned Roslyn compiler. Visual Studio is not required."
    }
    $missingFramework = @(Get-MissingFrameworkReferences)
    if ($missingFramework.Count -eq 0) {
        Add-Check "dotnet_framework_runtime" "pass" "Required .NET Framework x64 runtime references are available."
    } else {
        Add-Check "dotnet_framework_runtime" "fail" "Required .NET Framework x64 runtime references are missing." @{ missing = $missingFramework }
        Add-Blocker "DOTNET_FRAMEWORK_RUNTIME_MISSING" "The C# runtime cannot be built or started." "Install the .NET Framework 4.8.1 Developer Pack or run Configure with explicit system-package authorization. Visual Studio is not required."
    }
    $script:NuGet = Find-NuGet
    if ($null -eq $script:NuGet) {
        Add-Check "nuget" "fail" "NuGet CLI was not found."
        Add-Blocker "NUGET_NOT_READY" "C# packages cannot be restored." "Run Configure to acquire the pinned signed NuGet CLI."
    } else {
        Add-Check "nuget" "pass" "NuGet CLI is available." @{ path = $script:NuGet; sha256 = Get-FileSha256 $script:NuGet }
    }
    $script:Interop = Find-InteropDirectory
    if ($null -eq $script:Interop) {
        Add-Check "solidworks_interop" "fail" "Required SolidWorks Interop assemblies were not found."
        Add-Blocker "SOLIDWORKS_EXTERNAL_PREREQUISITE" "The external SolidWorks installation is absent or its Interop directory cannot be resolved." "Install/repair and license SolidWorks outside this configuration stage, or pass its Interop directory."
    } else {
        $script:ApiRedist = Find-ApiRedistDirectory $script:Interop
        if ($null -eq $script:ApiRedist) {
            Add-Check "solidworks_interop" "fail" "Core Interop assemblies exist, but SolidWorks.Interop.cosworks.dll is missing." @{ path = $script:Interop }
            Add-Blocker "SOLIDWORKS_API_REDIST_MISSING" "The external SolidWorks API redist is incomplete." "Repair SolidWorks or pass -SolidWorksApiRedistDirectory."
        } else {
            Add-Check "solidworks_interop" "pass" "SolidWorks Interop and API redist assemblies are available." @{ interop = $script:Interop; api_redist = $script:ApiRedist }
        }
    }
    $execution = Join-Path $script:RepoRoot "solidworks-execution\SolidworksExecution\bin\$Configuration\SolidworksExecution.exe"
    $helper = Join-Path $script:RepoRoot "solidworks-execution\SolidworksExecution\bin\$Configuration\HostBootstrap\SolidWorksHostBootstrap.exe"
    foreach ($row in @(@("execution_service", $execution), @("host_bootstrap_helper", $helper))) {
        if (Test-Path -LiteralPath $row[1] -PathType Leaf) {
            Add-Check $row[0] "pass" "Required build output is present." @{ path = $row[1]; sha256 = Get-FileSha256 $row[1] }
            $script:Outputs[$row[0]] = $row[1]
        } else {
            Add-Check $row[0] "fail" "Required build output is missing." @{ path = $row[1] }
            Add-Blocker "BUILD_OUTPUT_MISSING" "Required build output is missing: $($row[1])" "Run Configure after satisfying the build prerequisites."
        }
    }
}

function Invoke-Verify {
    if ($script:Blockers.Count -gt 0) {
        Add-Action "verify_repository_runtime" "not_run" "Verification did not run because readiness checks are blocked."
        return
    }
    $importProbe = & $script:Python.executable -c "import fastmcp,httpx,jsonschema,dotenv; print('ok')" 2>&1
    if ($LASTEXITCODE -ne 0 -or @($importProbe)[-1] -ne "ok") {
        Add-Blocker "PYTHON_DEPENDENCIES_INVALID" "The repository Python dependencies cannot be imported." "Rerun Configure using the appropriate lock file."
        return
    }
    Add-Action "verify_python_dependencies" "completed" "Imported the required MCP runtime dependencies."
    $mcpVerifier = Join-Path $script:RepoRoot "scripts\verify_repository_mcp.py"
    $mcpJson = & $script:Python.executable $mcpVerifier --repository-root $script:RepoRoot --timeout-seconds 30 2>&1
    if ($LASTEXITCODE -ne 0) {
        Add-Blocker "MCP_STDIO_VERIFY_FAILED" "The repository Codex stdio MCP did not initialize with the contracted tool surface." "Review the MCP verifier output and restart Codex only after it passes."
        Add-Action "verify_mcp_stdio" "failed" "MCP stdio verification failed." @{ output = [string]($mcpJson -join "`n") }
        return
    }
    try { $mcpResult = @($mcpJson)[-1] | ConvertFrom-Json } catch { throw "MCP verifier returned invalid JSON." }
    Add-Action "verify_mcp_stdio" "completed" "Initialized the Codex stdio MCP and matched the semantic-tool contract." $mcpResult
    $nativeDirectory = Join-Path $script:ReportRoot "native-host-inspect"
    New-Item -ItemType Directory -Path $nativeDirectory -Force | Out-Null
    $helper = $script:Outputs["host_bootstrap_helper"]
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $helper --output-dir $nativeDirectory --skip-solidworks-launch --no-regserver 2>$null | Out-Null
    $nativeExit = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    $nativeReportPath = Join-Path $nativeDirectory "host-preflight-report.json"
    if (-not (Test-Path -LiteralPath $nativeReportPath -PathType Leaf)) {
        Add-Blocker "NATIVE_HOST_INSPECT_MISSING_REPORT" "The native no-launch host inspection produced no report." "Rebuild the C# service and HostBootstrap helper."
        return
    }
    $nativeReport = Get-Content -LiteralPath $nativeReportPath -Raw | ConvertFrom-Json
    $script:Outputs["native_host_report"] = $nativeReportPath
    $script:Outputs["native_host_report_sha256"] = Get-FileSha256 $nativeReportPath
    if ($nativeExit -eq 0 -and $nativeReport.status -in @("pass", "warning")) {
        Add-Action "verify_native_host_inspect" "completed" "The repository-owned native no-launch inspection completed." @{ status = $nativeReport.status }
        if ($nativeReport.status -eq "warning") { Add-Warning "NATIVE_HOST_WARNING" "Native host inspection completed with warnings; review its report." }
    } else {
        Add-Action "verify_native_host_inspect" "failed" "Native host inspection reported a blocker." @{ status = $nativeReport.status; blocking_issues = $nativeReport.blocking_issues }
        Add-Blocker "NATIVE_HOST_INSPECT_BLOCKED" "The repository is configured, but the external SolidWorks host preflight remains blocked." "Review the native host report. SolidWorks installation/licensing and interactive-desktop issues remain external to this setup stage."
    }
}

$unhandled = $null
try {
    if ($Mode -eq "Configure") { Invoke-Configure }
    Collect-ReadinessChecks
    if ($Mode -eq "Configure" -and $script:PublishSetupState -and $script:Blockers.Count -eq 0) {
        Write-SetupState $script:SetupFingerprint
    }
    if ($Mode -eq "Verify") { Invoke-Verify }
} catch {
    $unhandled = $_.Exception.Message
}
Write-SetupReport $unhandled
if ($script:FinalStatus -eq "blocked") { exit 2 }
exit 0
