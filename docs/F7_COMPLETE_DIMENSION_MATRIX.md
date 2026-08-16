# F7 complete DimensionPlan qualification matrix

Status: complete offline preparation and fail-closed live runner are implemented; new 18-kind
SolidWorks evidence still must be generated before production capability promotion.

## Positive matrix design

F7 uses six distinct immutable dimension handoffs in the canonical category order: plate,
shaft/sleeve, bracket, flange, slot/cavity and threaded. The six cases must bind at least five
distinct source models and may not reuse one proxy handoff for multiple categories.

The recommended engineering distribution is:

| Category | DimensionPlan kinds |
|---|---|
| plate | `linear`, `aligned`, `overall` |
| shaft/sleeve | `diameter`, `radius`, `boss`, `symmetric` |
| bracket | `angular`, `step`, `reference` |
| flange | `hole_diameter`, `hole_quantity`, `hole_spacing`, `hole_group_location` |
| slot/cavity | `slot`, `fillet` |
| threaded | `hole_depth`, `chamfer` |

The repository does not infer these bindings from category labels. Each advanced recipe must name
the exact handoff source IDs, visible attachment entities, feature IDs, target view, dimension
zone, value, display format and verification tolerances. The preparation gate requires all 18
kinds exactly once across the six plans.

Recipe version `1.1` supports all three trusted source tiers:

- `model_or_pmi`, whose nominal must exist in a frozen model-driven dimension or PMI record;
- `user_confirmed_input`, whose nominal and any tolerance/fit values must match immutable approved
  input records; and
- `reference_geometry_measurement`, which must remain non-manufacturing and bind the exact frozen
  view/entities.

At least one case must carry an approved tolerance and at least one exact prefix or suffix. Every
case necessarily exercises persistent attachments, annotation positions and save/close/read-only
reopen identity; at least one model-driven source exercises native model-dimension import. Together
these are the six F7 production execution elements.

## Preparation contract

`dimension-f7-preparation-request.schema.json` binds the F0 summary, six handoff/recipe artifacts,
new drawing/evidence outputs and the new matrix-request path. Run:

```powershell
.\.venv\Scripts\python.exe .\scripts\prepare_dimension_f7_live_matrix.py `
  --request C:\path\to\dimension-f7-preparation-request.json
```

The preparer is COM-free. It verifies all hashes and engineering gates before publication,
requires exact 18-kind and six-element coverage, snapshots all upstream inputs, publishes one
immutable `dimension_plan.json` per handoff, rechecks input hashes, and publishes the matrix request
last. Existing plan/output/evidence paths fail closed.

The live runner then consumes the generated request through the current 24-tool/zero-prompt
semantic MCP:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_dimension_f7_live_matrix.py `
  --request C:\path\to\dimension-f7-matrix-request.json `
  --summary-output C:\path\to\dimension-f7-summary.json `
  --promotion-candidate-output C:\path\to\dimension-capabilities.candidate.json `
  --execution-service-path D:\solidworks-mcp\solidworks-execution\SolidworksExecution\bin\Debug\SolidworksExecution.exe `
  --execution-pid 12345
```

Before COM, the runner now rejects missing kinds/elements, fewer than five distinct source models,
handoff proxy reuse, request/plan binding drift, changed artifacts, output collisions and any
non-production planning request. Each accepted case must still pass native creation, in-memory
readback, save/close/read-only reopen and an independent verification transaction.

## Promotion boundary

The runner never edits `dimension_planner/capabilities/current.json`. A complete summary may only
produce a separate reviewable candidate. Promotion requires checking that all six category counts,
18 kind counts and six execution-element counts are non-zero and that every case retained exact
source hashes and independent persisted fingerprints. Until the new live matrix passes and the
candidate is reviewed and promoted, production create remains `capability_blocked`.
