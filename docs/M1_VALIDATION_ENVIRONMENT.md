# M1 Isolated Validation Environment

Status: experimental, not releasable

## Isolation boundaries

- Worktree: `C:\Users\zarch\Desktop\solidworks-mcp-m1-validation`
- Branch: `experiment/semantic-feature-coverage-validation`
- Source models: `C:\Users\zarch\Desktop\solidwokrs-mcp-test` (read-only)
- Python environment: `.venv`
- Latest built runtime: `outputs\m1-isolated-runtime-20260812-r20`
- Latest runtime SHA-256:
  `dc0ff91a846fcfbcee7f7bfa17fabf0a92f082bd55a31b853d2f80a41fbc004c`
- Complete five-model evidence: `outputs\m1-five-models-experimental-20260812-r6`
- Focused circular-cut evidence: `outputs\m1-five-models-experimental-20260812-r4\model-1`
- Focused pattern-axis evidence: `outputs\m1-pattern-validation-20260812-r1`
- Focused six-occurrence evidence: `outputs\m1-occurrence-validation-20260812-r1`
- Focused through-hole depth evidence: `outputs\m1-through-depth-validation-20260812-r1`
- Focused identity/circular-pattern evidence: `outputs\m1-depth-aware-lineage-validation-20260812-r1`
- Focused cosmetic-thread evidence: `outputs\m1-thread-edge-validation-20260812-r1`

The worktree contains only the M1 semantic initializer changes. G0 annotation-probe sources,
reports, private executor operations, and unrelated working-tree changes are intentionally absent.

The earlier runtimes and rejected publications are retained as immutable diagnostic records. The
current complete five-model evidence still comes from r18. Its executable SHA-256 is
`82b75715b1ff15383799d088b45217833178e72d0ebc47bd4ed6ea9ca176ed52`.
It includes typed feature classification, frozen B-Rep identity binding, depth-aware COM lineage,
native circular-transform axis/occurrence extraction, and fail-closed pattern/mirror publication.

## Session startup

Start a new Codex CLI process with this worktree as its current directory. The new process must use
this worktree's `.codex\config.toml` and `.venv`; an existing Codex process may retain the previous
MCP tool schema and must not be used for the M1 live matrix.

Before live initialization, confirm that the discovered `initialize_part_drawing_handoff` schema
contains `semantic_feature_profile` and that `solidworks_status` reports `ok=true` and
`com_attached=true`.

Stop the currently running repository Execution Service only when beginning the live matrix, then
start the selected immutable runtime from its own working directory. Port `5000` is machine-global;
do not run the old and isolated services concurrently.

## Evidence rules

- Use `semantic_feature_profile=m1-experimental` for all five models.
- Use a new empty child publication directory for every model and attempt.
- Never reuse or overwrite initializer artifacts.
- Record source SHA-256 before and after each transaction.
- Require `COMPLETED`, `verified=true`, and `handoff_integrity=pass`.
- Require both semantic artifacts, their manifest bindings, and valid SHA-256 values.
- Treat `status=incomplete` and every open question as extraction-gap evidence, not success.
- Treat missing optional controlled significance as an `optional_controlled_input` open question;
  it does not invalidate the handoff or block geometric inference, but it must block closed-set
  coverage.
- Do not release until the full repository test matrix and applicable live gates pass.

## Validation record

- Five-model live initializer matrix: `5/5` completed, verified, integrity pass.
- Source-model SHA-256 after matrix: `5/5` unchanged.
- Semantic/taxonomy manifest bindings: `5/5` pass.
- Typed features extracted in the r6 matrix: `32`.
- Frozen occurrences / relations / through-hole extents / independent threads: `9 / 5 / 3 / 4`.
- Python repository tests before final handoff: see the latest command record.
- Compiler tests: `36/36 passed`.
- Python compileall: passed.
- ViewPlan C# contract tests include the M1 deterministic classifier contract.
- Focused live matrix: model 2 publishes linear-pattern and mirror relations; model 3 publishes
  its mirror relation and three B-Rep-bound cosmetic threads; model 4 publishes its circular axis
  plus three unique rotated M16 occurrences. All three handoffs passed integrity.
- Roslyn x64 r18 runtime build: passed with one existing `CS1701` assembly-version unification warning.
- Latest repository regression: Python `166 passed, 7 subtests passed`; compiler `36/36`;
  ViewPlan C# contract tests `48/48`; compileall and `git diff --check` passed.
- r20 Roslyn x64 runtime build: passed with the same existing `CS1701` warning.
- No-launch host inspection report:
  `.host-preflight-m1-20260812-r7\host-preflight-report.json`, status `pass`, SHA-256
  `d7b873cdea24060afb92df96fd064ec542070caf44a95d95232196867bfaeedc`, no warnings or blockers.
- Final r20 HoleSpecification live evidence:
  `outputs\m1-hole-spec-validation-20260812-r2`. The Execution Service executable path resolved to
  r20 and its SHA-256 was
  `dc0ff91a846fcfbcee7f7bfa17fabf0a92f082bd55a31b853d2f80a41fbc004c`.
  Models 3/4/5 returned `COMPLETED`, `verified=true`, and `handoff_integrity=pass`; their manifest
  SHA-256 values are respectively `348ee012f659373700e76a2419cbc75d603b000d219d8bed890430f7f37fb6cd`,
  `05a111de9b1a978a1268728a101cb66760542367659d23a799ea9fc1e75d0887`, and
  `089d47265bd372d073ea591e477e0bb77e074a993c7f3530ab2e257934e1f4d3`.
- The live matrix proves typed Hole Wizard M8/M16 getter publication on models 3/4 and circular
  extruded-cut through/blind end-condition and depth publication on model 5. All three source model
  hashes remained unchanged, and every semantic/taxonomy manifest binding re-hashed exactly.
- Final COM bootstrap report: `.host-preflight-m1-20260812-r8\host-preflight-report.json`, status
  `pass`, SHA-256 `b609abff416587a7aa40b40de51332e258c4bde42923297023d590ef43fed141`,
  with no warnings or blockers.
- M1 software and live HoleSpecification gates are closed. Controlled PMI/significance remains an
  optional external-input question and continues to block semantic-complete closed-set coverage;
  it is not represented as extracted source evidence.
