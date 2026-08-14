# Bolt-gauge examples

For repository installation before the semantic MCP can start, use
`setup_repository_host.ps1`; its `Inspect`, `Configure`, and `Verify` modes are documented in
[`docs/REPOSITORY_HOST_SETUP.md`](../docs/REPOSITORY_HOST_SETUP.md). This is separate from the
SolidWorks-specific `bootstrap-solidworks-host` Skill.

## Dimension F0 contract tests

First freeze a read-only model/drawing corpus and generate one probe request per exact-basename
pair:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_dimension_f0_corpus_manifest.py `
  --input-dir C:\path\to\dimension-corpus `
  --output-dir C:\temp\dimension-f0-corpus `
  --repository-root (Get-Location).Path
```

Run the independent COM-free dimension probe contract suite in a new or empty output directory:

```powershell
.\scripts\run_dimension_contract_tests.ps1 `
  -RepositoryRoot (Get-Location).Path `
  -OutputDirectory C:\temp\dimension-contract-tests `
  -ProbeRequestDirectory C:\temp\dimension-f0-corpus\probe-requests
```

This suite is additive to the existing 45 ViewPlan C# contract tests. It does not contact
SolidWorks and does not write under the source corpus or `validation/`.

To add a production-frozen case, build one request from an independently verified ViewPlan
drawing and its transaction sidecar. The builder verifies the sidecar's drawing path, drawing
hash, canonical plan hash, and `verified=true` marker before publishing a new request:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_dimension_f0_frozen_request.py `
  --view-plan C:\path\to\view_plan.json `
  --verified-drawing C:\path\to\verified.SLDDRW `
  --verification-sidecar C:\path\to\verified.SLDDRW.verification.json `
  --publication-directory C:\temp\dimension-f0-corpus\live\frozen-case `
  --output-request C:\temp\dimension-f0-corpus\probe-requests\frozen-case.json
```

After a no-repair host preflight passes and the rebuilt C# Execution Service is running, execute
the hash-bound requests through the research-only F0 endpoint and evaluate every emitted report:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_dimension_f0_live_probes.py `
  --probe-request-directory C:\temp\dimension-f0-corpus\probe-requests `
  --summary-output-directory C:\temp\dimension-f0-live-summary
```

The Python client only orchestrates HTTP and evaluates evidence. All SolidWorks COM calls remain in
the repository C# service on its STA thread. The endpoint is not registered as an Agent-visible MCP
tool and is not part of the production DimensionPlan surface.

## Legacy examples

These scripts record the SolidWorks 2026 development and verification flow used
to exercise SolidPilot's part-modeling tools. They are examples, not an
automated test suite, and they operate on a live SolidWorks session.

- `gauge_v4.py` is the latest complete gauge build.
- `gauge_stage*.py` and `gauge_v2_stage*.py` preserve smaller diagnostic stages.
- `gauge_text_test*.py`, `gauge_numbers_a.py`, and
  `gauge_extrude_text.py` document the sketch-text investigation. New clients
  should prefer the public `add_sketch_text` MCP tool over these low-level COM
  experiments.

Run examples with the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe .\scripts\gauge_v4.py
```

Generated parts, drawings, and captures default to the ignored `outputs/`
directory. Override that destination without editing a script:

```powershell
$env:SOLIDPILOT_OUTPUT_DIR = "C:\CAD\SolidPilot Outputs"
.\.venv\Scripts\python.exe .\scripts\gauge_v4.py
```

All geometry values passed to SolidPilot are in metres. Review a script before
running it: several diagnostic stages assume that a particular part or sketch
is already active.
