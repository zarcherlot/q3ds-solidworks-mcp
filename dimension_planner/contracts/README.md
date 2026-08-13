# Dimension planning contracts

F0 currently owns two research contracts:

- `dimension-api-probe.schema.json` freezes the complete SolidWorks 2025 SP5 probe request.
- `dimension-api-evidence.schema.json` validates immutable native-API evidence.

They are not DimensionPlan production contracts and must not be registered as Agent-visible MCP
tools.

F1 adds two production handoff contracts without introducing DimensionPlan itself:

- `dimension-planning-handoff-request.schema.json` binds one independently verified ViewPlan
  drawing and any explicitly approved user inputs.
- `dimension-planning-handoff.schema.json` freezes the read-only native geometry, dimension,
  PMI, feature, annotation, provenance, and immutability readback published manifest-last.

The F1 endpoint remains private to the repository execution service until the semantic initializer
tool and `solidworks-dimension-drawing` Skill are introduced in F6.

F2 freezes four production planning contracts:

- `dimension-plan.schema.json` defines the strict immutable DimensionPlan 1.0 union.
- `dimension-planning-request.schema.json` binds a planning run to one immutable F1 handoff.
- `dimension-planning-result.schema.json` separates engineering publication from execution
  readiness, including the valid `capability_blocked` outcome.
- `dimension-executor-capabilities.schema.json` versions the complete dimension-kind and shared-
  element execution capability registry, with live evidence required for final conclusions.

F2 still exposes no Agent-visible dimension tool and performs no SolidWorks mutation.

F3 implements the fixed, fail-closed `integrity -> schema -> source -> attachment -> semantics ->
coverage -> redundancy -> layout -> capability` validation sequence. The first eight gates decide
engineering validity; capability is assessed separately so a valid plan can be atomically published
as `capability_blocked`. Native creation and persisted readback remain F4-F5 work.

F4 adds `dimension-drawing-verification.schema.json` for the no-overwrite C# transaction sidecar.
It binds the committed drawing to the immutable DimensionPlan file/canonical hashes, all frozen
inputs, stable native handles, and matching in-memory/read-only-reopen dimension snapshots.

F5 keeps DimensionPlan 1.0 immutable and completes its 18-kind native compiler union. Advanced
hole/slot/pattern, chamfer/fillet, baseline/chain and ordinate intent are resolved only from frozen
handoff geometry/features. Numeric, limit and fit tolerances are executable only when C# rebinds
every value or exact fit code to user-approved handoff inputs. The verification sidecar additionally
freezes hole-callout variables, complete logical text, tolerance readback and chain display state.

F7 adds three evidence contracts and two qualification-only engineering-semantic tools. They do
not weaken the production capability gate:

- `dimension-f7-matrix-request.schema.json` binds the six required part categories to published
  DimensionPlan/request hashes and all-new output/evidence paths.
- `dimension-f7-case-evidence.schema.json` binds one public validate/create/verify chain to the
  exact executor, capability manifest, output, verification sidecar and frozen upstream hashes.
- `dimension-f7-summary.schema.json` records exact six-category, 18-kind and shared execution-
  element coverage. Only a complete summary can produce a separate capability-promotion candidate.

`qualify_dimensioned_part_drawing` and `verify_qualified_dimensioned_part_drawing` are strictly
matrix-bound bootstrap transactions. They may execute `planned` capabilities only for F7 evidence,
reject any known-`unsupported` capability before COM, and never mutate `current.json`. Production
create/verify still require `supported + live + evidence_sha256`.

`scripts/run_dimension_f7_live_matrix.py` consumes these contracts through the repository's default
17-tool stdio MCP. It never calls executor-private verbs and never overwrites `current.json`; a
promotion candidate remains a reviewable immutable artifact until the complete live matrix passes.

The first-draft helpers are intentionally outside the production publication boundary.  A strict
recipe may bind exact frozen handoff records into one candidate, while the provisional six-category
profile emits six separately validated candidates covering the complete 18-kind union.  Proxy
category output is always labelled ineligible for F7 promotion and is never named
`dimension_plan.json`; production publication still requires the semantic MCP tool and authentic
per-category handoffs.
