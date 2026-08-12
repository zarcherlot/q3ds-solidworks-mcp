# Repository-owned SolidWorks host bootstrap

The default semantic MCP surface owns its deep host preflight. It does not depend on an external
Codex Skill, Python COM packages, generated makepy wrappers, or SolidWorks Interop assemblies in the
native helper.

## Runtime boundary

```text
inspect_solidworks_host / bootstrap_solidworks_host
  -> Python input validation and semantic orchestration
  -> POST /host/bootstrap on the C# Execution Service
  -> controlled x64 SolidWorksHostBootstrap.exe
  -> host-preflight-report.json + SHA-256
```

`/host/bootstrap` is a lifecycle endpoint. It does not pass through `/api/tool/execute` or the COM
STA dispatcher, and it accepts no executable path, command, free-form arguments, or caller-selected
lock path. SolidWorks COM activation occurs only in the native helper's isolated child process.

## Semantic modes

- `inspect_solidworks_host` fixes the native mode to no-launch/no-registration-repair. It checks the
  host, both registry views, detected installations, type library and Interop inventory, an optional
  `.DRWDOT`, and report-directory create/read/write/delete access.
- `bootstrap_solidworks_host` performs bounded isolated COM verification. Existing SolidWorks
  sessions are preserved, and only a newly created/owned probe session may be closed.
- `allow_registration_repair=true` is the sole route to one bounded `/regserver` attempt. The
  Execution Service must already be elevated; neither MCP nor the helper attempts elevation.

Both tools may overwrite `host-preflight-report.json` in the explicitly supplied existing output
directory. They never open, read, or modify business `.SLDPRT`, `.SLDASM`, or `.SLDDRW` files.

## Build and contract check

```powershell
scripts\build_host_bootstrap.ps1 -Configuration Debug
scripts\run_host_bootstrap_contract_tests.ps1 -Configuration Debug
```

Building `SolidworksExecution.csproj` also builds the helper and deploys it to the controlled
`bin/<Configuration>/HostBootstrap/` location resolved by the Execution Service.
