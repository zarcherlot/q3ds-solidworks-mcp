---
name: bootstrap-solidworks-host
description: Inspect, verify, diagnose, and explicitly repair a Windows SolidWorks automation host through the repository-owned solidpilot semantic MCP tools. Use before SolidWorks automation, after installation, upgrade, machine/account migration, COM activation or registration failure, when validating an optional .DRWDOT and writable report location, or when replacing the retired solidworks-host-bootstrap CLI Skill. Supports no-launch inspection, isolated bounded COM verification, and explicitly authorized one-time registration repair.
---

# Bootstrap SolidWorks Host

Use only the repository-owned semantic MCP tools `inspect_solidworks_host` and
`bootstrap_solidworks_host`. Keep COM and process control inside the C# Execution Service and its
native x64 helper.

## Choose the operation

1. Use `inspect_solidworks_host` when the user asks to inspect, diagnose, preflight, inventory, or
   validate the host without launching SolidWorks.
2. Use `bootstrap_solidworks_host` with `allow_registration_repair=false` when the user asks to
   bootstrap, verify, test COM activation, or confirm automation readiness.
3. Use `bootstrap_solidworks_host` with `allow_registration_repair=true` only when:
   - verification produced evidence consistent with COM registration failure; and
   - the user explicitly authorized registration repair in the current request.

Never infer repair authorization from a general request to inspect, diagnose, bootstrap, or make
the host ready. Never elevate a process. Repair requires an Execution Service that the user already
started elevated.

## Prepare inputs

- Require an existing absolute output directory. Prefer a user-supplied directory. Otherwise create
  a dedicated `.host-preflight` directory inside the current workspace and tell the user where the
  report will be written.
- Pass `drawing_template_path` only for an existing absolute `.DRWDOT` the user wants validated.
- Keep `visible=false` unless the user explicitly needs to observe the probe.
- Keep `keep_solidworks_running=false` unless the user explicitly wants a newly created probe
  session left running. A pre-existing user session is preserved regardless.
- Keep `com_timeout_seconds=180` and `regserver_timeout_seconds=120` unless host evidence justifies
  another bounded value.
- Warn that the tool replaces `host-preflight-report.json` in the selected directory. Use a new
  directory when the prior report must be preserved.

## Run and interpret

1. Call exactly one selected semantic tool; do not reproduce its checks in Python or shell.
2. Treat `pass` as ready, `warning` as usable with reported caveats, and `blocked` as not ready.
3. Report the returned report path and report SHA-256. Summarize `blocking_issues`, `warnings`, and
   relevant `actions`; do not claim success from HTTP success alone.
4. For a blocker caused by a restricted/non-interactive desktop, Session 0, or an unavailable MCP
   endpoint, explain the required environment change and stop.
5. For a repair-elevation blocker, tell the user to restart the repository Execution Service from an
   unrestricted interactive desktop with elevation, then request repair again. Do not attempt to
   elevate or bypass the gate.
6. Do not repeatedly retry the same blocker. Preserve the report as diagnostic evidence.

## Hard boundaries

- Do not invoke a legacy bootstrap Skill, external bootstrap path, helper executable, arbitrary CLI
  arguments, `/regserver`, PowerShell COM, Python COM, pywin32, makepy, or SolidWorks Interop.
- Do not route bootstrap through low-level executor operations or `/api/tool/execute`.
- Do not open, read, modify, or create business `.SLDPRT`, `.SLDASM`, or `.SLDDRW` files.
- Do not substitute `solidworks_status` for deep host preflight. It remains a lightweight lifecycle
  check and may be used separately only when the user asks for current session status.
- If the semantic tools are absent, ask the user to rebuild/restart the repository MCP session so
  `.codex/config.toml` can discover them. Do not fall back to the retired workflow.
