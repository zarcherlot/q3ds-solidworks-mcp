# Repository host setup before MCP

`scripts/setup_repository_host.ps1` prepares and verifies the repository runtime before Codex or
another client attempts to start the `solidpilot` semantic MCP. It resolves the bootstrap cycle in
which the MCP cannot diagnose or repair a missing Python environment because Python is required to
start that MCP.

This stage is separate from `bootstrap-solidworks-host`:

```text
setup_repository_host.ps1
  -> repository Python, dependencies, C# packages and build outputs
  -> stdio MCP discovery verification
  -> repository-owned native no-launch host inspection

$bootstrap-solidworks-host
  -> semantic MCP
  -> isolated bounded SolidWorks COM verification
  -> explicitly authorized one-time registration repair, when justified
```

## Modes

Run from the repository root in PowerShell:

```powershell
# Diagnostic-only inventory; the report is its only write. Exit 2 means it contains blockers.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_repository_host.ps1 -Mode Inspect

# Repository-scoped configuration for an end-user installation.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_repository_host.ps1 -Mode Configure

# Use the development/test dependency lock instead.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_repository_host.ps1 `
  -Mode Configure -DependencySet Development

# Verify imports, stdio MCP discovery and native no-launch inspection without launching SolidWorks.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_repository_host.ps1 -Mode Verify
```

Every mode atomically publishes `.host-setup/repository-host-setup-report.json` by default. The
report follows `scripts/contracts/repository-host-setup-report.schema.json`. Status is `pass`,
`warning`, or `blocked`; exit code 2 corresponds to `blocked`.

## Configure behavior

Without additional authorization, `Configure` may only change ignored or build-output paths inside
the repository. It can:

- create `.venv` from an existing Python 3.12 x64 runtime;
- bootstrap pip with the Python standard library and install a selected hash-locked dependency set;
- create the ignored `adapters/claude/.env` from `.env.example` without overwriting an existing file;
- download the pinned NuGet 6.11.1 CLI into `.host-setup/tools`, after validating its Microsoft
  Authenticode signature;
- restore fixed C# packages;
- restore and use the pinned repository-local Roslyn 4.14.0 compiler; Visual Studio is not required;
- build the x64 Execution Service and native HostBootstrap helper.

After a successful Configure run, the script atomically writes
`.host-setup/repository-host-setup-state.json`. The state fingerprints the machine, Python runtime,
selected dependency lock, C# sources, build script, .NET Framework references and SolidWorks
Interop assemblies, and records hashes for the prepared outputs. A later Configure run skips pip,
NuGet and C# compilation when that fingerprint and every output hash still match. Missing or changed
inputs invalidate the state and trigger the necessary setup again.

The Codex stdio launcher invokes this idempotence gate before starting MCP. On the first launch,
identified by a missing state file, it authorizes installation of only the contracted Python and
.NET Framework prerequisites. Later launches do not carry system-install authorization and normally
finish at the fast state check. The client startup timeout allows the first dependency restore and
native build to finish.

Pass nonstandard SolidWorks API locations explicitly when registry discovery cannot resolve them:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_repository_host.ps1 `
  -Mode Configure `
  -SolidWorksInteropDirectory 'D:\SolidWorks 2025\SOLIDWORKS' `
  -SolidWorksApiRedistDirectory 'D:\SolidWorks 2025\SOLIDWORKS\api\redist'
```

`-AllowSystemPackageInstall` explicitly authorizes `winget` installation of a missing Python 3.12
runtime or .NET Framework 4.8.1 Developer Pack. It never installs Visual Studio or Visual Studio
Build Tools. The script never elevates itself; organizational policy or an installer may still
require the user to run an appropriate shell. System installation is not attempted without this
switch.

## Hard boundaries

This stage does not:

- install, repair, update or license SolidWorks;
- launch SolidWorks or activate COM;
- run `/regserver`;
- create or modify business `.SLDPRT`, `.SLDASM` or `.SLDDRW` documents;
- modify Codex user-global configuration;
- start a persistent Execution Service;
- elevate the current process.

After `Verify` passes, restart the Codex/MCP session so `.codex/config.toml` discovers the prepared
stdio server, then invoke `bootstrap-solidworks-host` for the SolidWorks-specific COM readiness gate.
