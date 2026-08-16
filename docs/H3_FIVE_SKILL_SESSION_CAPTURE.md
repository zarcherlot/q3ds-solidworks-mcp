# H3 append-only five-Skill session capture

Status: session creation, ordered semantic-response capture, stage artifact freezing and H1
candidate assembly are implemented without initiating any SolidWorks call.

## Session creation

H3 accepts only a SHA-bound H2 report whose status is `ready` and whose blocker list is empty. It
rechecks the current clean Git commit, H0 report, execution-service binary, source model and drawing
template at creation and before every later capture. It also recomputes the entire output namespace and exact 16-operation schedule rather
than trusting paths copied into the report.

Only then does H3 create the new session root and its fixed initializer, view, dimension, layout,
response and stage directories. `session-manifest.json` is published once and never updated.

```powershell
.\.venv\Scripts\python.exe .\scripts\h3_five_skill_session.py create `
  --preflight C:\evidence\h2-session-preflight.json `
  --preflight-sha256 <sha256> `
  --repository-root D:\solidworks-mcp
```

## Append-only response capture

After each externally performed semantic MCP operation, capture its exact JSON response. H3 accepts
only the next tool in the frozen schedule and writes `responses/NN-tool-name.json` without
overwrite:

```powershell
.\.venv\Scripts\python.exe .\scripts\h3_five_skill_session.py capture-operation `
  --session-manifest C:\session\session-manifest.json `
  --session-sha256 <sha256> `
  --tool validate_part_drawing_view_plan `
  --response C:\capture\response.json
```

A failed semantic response is retained as immutable failure evidence. No later operation may be
captured in that session. The next stage cannot start until the preceding stage artifact manifest
has been frozen.

## Stage and final capture

After all operations for one stage succeed, provide a JSON object containing exact `inputs` and
`outputs` arrays of `{role, path}` objects:

```powershell
.\.venv\Scripts\python.exe .\scripts\h3_five_skill_session.py capture-stage `
  --session-manifest C:\session\session-manifest.json `
  --session-sha256 <sha256> `
  --order 3 `
  --artifacts C:\capture\view-stage-artifacts.json
```

H3 resolves every path, computes its SHA-256, requires the exact role set and planned output path,
and publishes one immutable stage capture. Outputs must remain inside the session root.

After 16 successful responses and five ordered stage captures, `finalize` re-hashes all frozen
artifacts, assembles the complete H1 candidate, passes it through the independent H1 validator and
publishes it once at the path frozen by H2:

```powershell
.\.venv\Scripts\python.exe .\scripts\h3_five_skill_session.py finalize `
  --session-manifest C:\session\session-manifest.json `
  --session-sha256 <sha256>
```

H3 is a capture layer, not an alternative executor. It never invokes MCP, HTTP, COM or UI
automation itself. Until F7 is promoted, a real H2 report cannot become `ready`, so no production
session can be created.
