# G1 immutable drawing-layout handoff

G1 consumes only a real DimensionPlan 1.0 drawing that was executed and independently verified by
the F-stage transaction. It never accepts mock annotations, ViewPlan dimension zones, an unverified
drawing, or a caller-selected capability registry.

## Frozen inputs and output

The request binds five immutable artifacts by absolute path and SHA-256:

- DimensionPlan 1.0
- dimensioned `.SLDDRW`
- dimension verification sidecar
- repository G0 capability manifest
- live-complete G0 qualification

The C# transaction owns all SolidWorks COM work. It requires the source drawing to be closed, opens
it read-only, captures actual boundaries before and after rebuild, closes it, opens it read-only a
second time, rebuilds and captures again. It does not call Save. Only after all five hashes are
unchanged does it atomically publish `drawing-layout-handoff.json`; that handoff is the last and only
published file.

The handoff freezes verified dimension IDs, SI values and model persistent references; actual sheet,
view, dimension, note, leader, label, section, center and title-block bounds when present; view
positions and locks; projection parentage/alignment; sheet-frame and title-block locked zones; and
repository-bounded object/frame/text spacing.

## Capability semantics

An observed object is collision-usable only when its native row is exact and the matching G0
capability is `supported`. Unsupported exactness is retained as structured evidence with
`collision_usable=false`; it is never upgraded from a deterministic approximation.

Therefore a valid handoff may be `capability_blocked`. This preserves a complete auditable input for
G2 while preventing G3/G4 from treating an unqualified boundary as safe.

## Live acceptance

SolidWorks 2025 SP5 (`33.5.0`) produced the final plate candidate at:

`C:\Users\admin\Downloads\solidwokrs-mcp-test-g1-20260815\plate-handoff-build2\drawing-layout-handoff.json`

Its SHA-256 is
`8f3d6d0346ad8ad1d48b63a847e0dd7c4cc630d0750e6cf3e0f849b4085ad672`. It freezes five actual
boundary objects, three view position/lock records and one verified dimension semantic record. The
before-rebuild, after-rebuild and read-only-reopen object hashes are identical. Every upstream
artifact hash is unchanged. Its single explicit blocker is `dimension_display_bounds`, matching the
G0 live qualification rather than introducing a new G1 failure.
