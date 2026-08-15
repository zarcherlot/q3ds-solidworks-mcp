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

## Layout G0 boundary probe

Run the additive COM-free G0 request contract suite in a new or empty directory:

```powershell
.\scripts\run_layout_contract_tests.ps1 `
  -RepositoryRoot (Get-Location).Path `
  -OutputDirectory C:\temp\layout-contract-tests
```

Run the independent COM-free G4/G5 DrawingLayoutPlan/compiler/create/verification preflight and
capability suite separately:

```powershell
.\scripts\run_drawing_layout_contract_tests.ps1 `
  -RepositoryRoot (Get-Location).Path `
  -OutputDirectory C:\temp\drawing-layout-g4-contract-tests
```

After the no-repair repository host preflight passes and a rebuilt Execution Service is running,
probe one independently verified dimension drawing without saving it:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_layout_g0_live_probe.py `
  --dimension-plan C:\path\to\dimension_plan.json `
  --dimensioned-drawing C:\path\to\dimensioned.SLDDRW `
  --verification-sidecar C:\path\to\dimensioned.SLDDRW.dimension-verification.json `
  --publication-directory C:\temp\layout-g0-case
```

The private endpoint publishes the request, three structured boundary snapshots, and the evidence
report last. The runner rechecks the three frozen input hashes and applies the repository evidence
gate. It never promotes `drawing_layout_planner/capabilities/current.json` automatically.

Build and run the fixed six-category G0 matrix from independently verified F7 case evidence:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_layout_g0_matrix_request.py `
  --f7-evidence-directory C:\path\to\matrix-live-r14 `
  --matrix-root C:\temp\layout-g0-six-category `
  --matrix-id G0-SIX-CATEGORY

.\.venv\Scripts\python.exe .\scripts\run_layout_g0_live_matrix.py `
  --matrix-request C:\temp\layout-g0-six-category\layout-g0-matrix-request.json `
  --summary-path C:\temp\layout-g0-six-category\layout-g0-matrix-summary.json `
  --execution-base-url http://127.0.0.1:5000
```

The matrix runner accepts only an HTTP loopback origin and explicitly bypasses ambient HTTP proxy
settings. It verifies all six persisted evidence files against their in-memory objects before it
publishes the aggregate summary once.

Verified ViewPlan drawings can be probed with `--view-plan` and `--view-drawing`. The isolated
title-block fixture builder is research-only and writes a new drawing plus a hash-bound manifest:

```powershell
.\scripts\build_layout_g0_title_block_fixture.ps1 `
  -RepositoryRoot (Get-Location).Path `
  -SourceDrawing C:\path\to\verified.SLDDRW `
  -SourceVerificationSidecar C:\path\to\verified.SLDDRW.verification.json `
  -PublicationDirectory C:\temp\g0-title-block-fixture `
  -SolidWorksInteropDirectory D:\SW\SOLIDWORKS
```

After the six-category matrix and at least three supplemental evidence files exist,
`qualify_layout_g0.py` publishes the immutable final qualification and a separate capability
promotion candidate. It fails closed if any catalog item remains neither exact-supported nor
live-qualified unsupported.

## Layout G1 immutable handoff

After a no-repair host preflight passes and the rebuilt Execution Service is running, initialize a
G1 handoff only from an executed and independently verified DimensionPlan drawing:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_layout_g1_live_handoff.py `
  --dimension-plan C:\path\to\dimension_plan.json `
  --dimensioned-drawing C:\path\to\dimensioned.SLDDRW `
  --verification-sidecar C:\path\to\dimensioned.SLDDRW.dimension-verification.json `
  --publication-directory C:\temp\layout-g1-handoff
```

The runner builds the same strict request as the semantic initializer, bypasses ambient proxies for
the local qualification call, and independently validates the final handoff and its returned hash.
The C# service performs all SolidWorks readback and never saves the upstream drawing.

## Layout G7 qualification matrix

`run_drawing_layout_g7_live_matrix.py` runs the immutable nine-positive/one-negative final-layout
matrix through the default semantic MCP. It verifies the exact running Execution Service image,
locks the 24-tool/zero-prompt surface, protects all recursive inputs, and publishes each evidence,
summary and optional capability candidate only to new paths. Qualification tools never edit the
production manifest and reject any `unsupported` G0 boundary before COM.

```powershell
.\.venv\Scripts\python.exe .\scripts\run_drawing_layout_g7_live_matrix.py `
  --request C:\path\to\drawing-layout-g7-matrix-request.json `
  --summary-output C:\path\to\drawing-layout-g7-summary.json `
  --execution-service-path D:\solidworks-mcp\solidworks-execution\SolidworksExecution\bin\Debug\SolidworksExecution.exe `
  --execution-pid 12345
```

G7 completed with the all-supported G0 `1.1.0` registry. The immutable matrix summary SHA-256 is
`91e95b5c34ad92ac422839d6eb5585983336117bae7dbfc113f8e68be1122ecc`; the generated capability
candidate was promoted only after a byte-for-byte comparison. Because matrix outputs are immutable
and production capabilities are now supported, any future rerun must use a new output root and an
explicitly prepared qualification context rather than overwriting the completed evidence.

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
