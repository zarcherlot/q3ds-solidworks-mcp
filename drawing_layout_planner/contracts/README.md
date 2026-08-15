# Drawing layout contracts

G0 is intentionally isolated from production layout execution. The probe
request binds either an independently verified dimension drawing, an independently
verified ViewPlan drawing, or the research-only title-block fixture manifest.
The evidence contract records read-only boundary
snapshots before rebuild, after rebuild, and after a full close/read-only
reopen. Neither contract is an Agent-visible execution surface.

No capability may move from `planned` to `supported` or `unsupported` until live SolidWorks
2025 SP5 evidence passes the deterministic evaluator. Missing object classes
remain `planned`; they are not inferred from screenshots or replaced with a
different annotation class.

`layout-boundary-matrix-request.schema.json` freezes exactly one verified F7
case for each of the six repository categories. The matrix summary revalidates
every source request and evidence file, then reports each G0 capability as
`covered`, `partial`, or `missing`. Only eleven `covered` rows can make the
matrix `complete`; the aggregator never edits the capability registry.

`layout-boundary-qualification.schema.json` binds that six-category matrix and
the supplemental special-view/title-block evidence. It is the only artifact
that can generate a registry promotion candidate. Stable native geometry with
no exact text glyph extent is recorded as `unsupported`, never promoted as an
exact collision boundary.

G1 adds `drawing-layout-handoff-request.schema.json` and
`drawing-layout-handoff.schema.json`. The handoff is a read-only, hash-bound
freeze of the verified dimension stage and all layout inputs; it does not
authorize any mutation.

G2 adds `drawing-layout-plan.schema.json` and
`drawing-layout-plan-capabilities.schema.json`. DrawingLayoutPlan 1.0 has an
exact eight-operation union and constant fail-closed manufacturing-semantic
policy. View movement, scale changes and sheet-format changes require explicit
authorization. The plan-capability registry is separate from the G0 boundary
registry and binds that registry by protocol, version and file SHA-256. A valid
plan can be published while `capability_blocked`, but cannot be executed until
all requested operations, every mandatory safety element and every required
G0 boundary are `supported` by bound live evidence.

G3 adds `drawing-layout-planning-request.schema.json` and
`drawing-layout-planning-result.schema.json`. Callers express ordered
preferences, priorities and authorization-bound scale/sheet candidates; they
cannot set runtime grid, search, safety or readability policy. The repository
ruleset resolves final coordinates and records its exact SHA-256 in the
DrawingLayoutPlan producer. The engine rechecks the G1 handoff and all five
upstream artifacts before solving, then runs the complete final-state gate set
before the G2 PlanStore can publish.

G4 adds `drawing-layout-verification.schema.json` plus a private C#
DrawingLayoutPlan parser, compiler, recursive immutable-input preflight and
execution-capability gate. The native transaction copies the dimension-stage
drawing to a random temporary drawing, performs no more than three complete
apply/rebuild/real-boundary-readback/collision cycles, saves and closes it,
then requires a read-only reopen fingerprint match before atomically committing
the new drawing and sidecar. These private executor verbs do not enlarge the
semantic MCP surface. The production operation and safety states remain
`planned` until the G7 live matrix qualifies them through the G5 verifier.

G5 locks the G4 sidecar Schema by SHA-256 and adds an independent verification
preflight plus a private read-only verifier. It recompiles the upstream
DimensionPlan, permits only its explicitly planned G4 position overlays, and
then independently rechecks dimension values, text, tolerances and model
persistent references. A fresh layout snapshot must have the exact G1 object
and view inventories, no dangling leader, boundary or collision violation, and
the same normalized fingerprint as both G4 stages. The verifier closes without
saving, rehashes the final drawing and reruns the entire COM-free preflight.

G7 adds immutable matrix-request, case-evidence and summary contracts for nine positive layout
scenarios and one unauthorized sheet-format rejection. Scenario labels are proven from the frozen
DrawingLayoutPlan, DimensionPlan and ViewPlan rather than trusted as metadata. Qualification-only
semantic routes may exercise `planned` G4/G5 capabilities but keep known-unsupported G0 boundaries
fail-closed. A complete ten-scenario summary is the only input that can produce a separate reviewable
layout-capability promotion candidate; no G7 helper edits a production manifest.
