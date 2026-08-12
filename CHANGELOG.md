# Changelog

## Unreleased

- Added the explicit upper-layer Skill planning route and
  `publish_validated_part_drawing_view_plan`. A current Codex model can now generate one ViewPlan
  candidate from the immutable repository pack and verified handoff without MCP Sampling or a
  separate API key; the semantic MCP still revalidates, assesses capability and atomically
  publishes without overwrite, while capability-blocked plans remain non-executable.
- Added the repository-owned drawing-handoff initializer as a seventh engineering-semantic MCP
  tool. One C# no-overwrite transaction now keeps all SolidWorks COM inside the execution service,
  restores the source document, creates and read-only-reopens a blank drawing, captures six real
  standard views, freezes B-Rep/readiness JSON, rolls back partial files, and publishes the verified
  SHA-256 manifest last. The Python adapter revalidates the complete handoff before returning its
  production `PlanningRequest`, and a dedicated Codex Skill chains that result into ViewPlan 1.4.
- Started the repository-owned ViewPlan PlannerEngine migration: added the staged development plan,
  strict planning request/result and provenance models, provider-neutral model gateway boundary,
  atomic no-overwrite PlanStore, and a versioned executor capability registry. The registry
  truthfully blocks ViewPlan execution until direct C# support and persisted readback are wired.
- Imported the complete ViewPlan 1.4 Schema as a SHA-256-locked repository runtime contract,
  removed PromptCompiler schema discovery from installed Skills, and added fail-closed handoff/hash,
  plan-binding and Draft 2020-12 validation gates.
- Completed the repository PlannerEngine orchestration boundary: verified all nine handoff artifacts
  before model use, bound allow-listed profiles and the complete manifest into the prompt envelope,
  added a strict timeout-aware provider gateway, and recorded request, candidate, and capability-
  manifest hashes in planning audit results.
- Completed the A5 deterministic ViewPlan 1.4 gates in fixed integrity, Draft 2020-12 Schema,
  semantics, feature-coverage and sheet-layout order. The gates now reject invalid graph/evidence,
  frozen geometry, projection/section, coverage and placement relationships, validate RFC 3339
  timestamps, short-circuit dependent checks as `not_run`, and bind producer/ruleset trust to the
  selected immutable repository prompt pack.
- Completed A6 with the engineering-semantic `plan_part_drawing_views` tool. It uses MCP Sampling
  with the exact ViewPlan 1.4 submission Schema, injects all nine hash-verified planning artifacts,
  records the client-selected model, rechecks hashes immediately before sampling, assesses executor
  capability without downgrade, and atomically publishes a no-overwrite `view_plan.json` only in
  the verified handoff directory.
- Completed B1 with a repository-native, COM-free C# ViewPlan 1.4 parser and private validation
  entry. The executor links the single repository Schema, verifies its locked SHA-256, enforces the
  Draft 2020-12 keywords used by that contract (including unions, conditionals and RFC 3339),
  rejects opaque strings and unknown request fields, and leaves every view capability `planned`.
- Started B2 with a COM-free basic-view execution compiler and a private native C# executor for
  standard/named `model_view` and parent-bound `projected_view`. The compiler topologically orders
  the frozen view DAG and fails unsupported view/element/orientation contracts before COM. Live
  SolidWorks 2025 SP5 checks verified localized standard/dimetric lookup, exact named orientation,
  roll application, deterministic view identities, unique parentage, and in-memory readback without
  saving or modifying `validation/`. Capabilities remain `planned` until B3 persisted reopen
  verification is complete.
- Completed the remaining B2 explicit-basis path with an executor-owned, read-only
  temporary-named-view transaction. It orthonormalizes the contracted model-space basis, verifies
  the exact SolidWorks orientation transform and drawing roll, deletes the temporary name, restores
  the source orientation, and rejects non-isolated or writable source models. Live testing confirmed
  zero temporary names and an unchanged source-model dirty flag after explicit-view creation.
- Completed B3 with a no-overwrite C# ViewPlan disk transaction. It verifies the absolute paths and
  SHA-256 hashes of ten frozen inputs before COM, creates only temporary/new drawing artifacts,
  saves, closes, read-only reopens, and verifies every planned view plus the sheet contract before
  atomically committing the drawing and audit sidecar. SolidWorks 2025 SP5 live tests passed twelve
  persisted cases covering all standard orientations, exact named and explicit-basis roll, shaded
  edges, and projected parentage; an independent second reopen also passed, source models remained
  clean, temporary names were removed, and `validation/` remained byte-identical. The executor
  capability registry is now 0.5.0 with `model_view` and `projected_view` marked `supported/live`.
- Completed B4 with repository-native `validate_part_drawing_view_plan`,
  `create_part_drawing_from_view_plan`, and `verify_part_drawing_view_plan` engineering tools. The
  default MCP surface now publishes the exact strict ViewPlan 1.4 schema, re-runs integrity/schema/
  semantics/coverage/layout and capability gates against the original `PlanningRequest`, and routes
  only supported plans to private C# validation, transaction, and independent read-only verification
  operations. The external Skill/CLI executor tools were removed from the default surface. A live
  SolidWorks 2025 SP5 service test verified COM-free validation, state `0 -> 1` commit, duplicate
  idempotency, state-preserving verification, stable missing-output rejection, and exact two-view
  save/close/reopen readback without modifying `validation/`.
- Completed C1 with native `full_section`, `half_section`, `offset_section`, `aligned_section`, and
  `removed_section` support in the private C# ViewPlan transaction. Added COM-free section-contract
  compilation, unique frozen-feature resolution, full-section feature-axis freezing, offset-path
  intersection gates, strict section labels, persistent unique handles, and normalized `IDrSection`
  fingerprints covering segments, partial/aligned/reversed state, placement alignment, scale, and
  depth semantics. All five families passed SolidWorks 2025 SP5 save/close/read-only-reopen and
  independent verification with stable pre/post-save fingerprints. Capability manifest 0.6.0 now
  marks the five section types and `view_labels` as `supported/live`; broken-out and detail views
  were kept blocked at the C1 checkpoint pending C2 evidence.
- Completed C2 with native circular `broken_out_section` and parent-derived circular `detail_view`
  support inside the private C# ViewPlan transaction. Added strict COM-free local-profile
  containment and reversal gates, native `CreateBreakOutSection`/`CreateDetailViewAt4` execution,
  persistent-handle matching, exact broken-out depth/boundary readback, complete detail style and
  show-type mappings, and normalized local-view fingerprints. SolidWorks 2025 SP5 passed the
  two-type save/close/read-only-reopen and independent verification matrix, plus an exact jagged
  shape-intensity case; non-jagged intensity is explicitly recorded as not applicable. Capability
  manifest 0.7.0 now marks both C2 view types as `supported/live` without exposing COM-shaped tools
  or modifying `validation/`.
- Completed C3 with native parent-derived `auxiliary_view` support inside the private C# ViewPlan
  transaction. Added COM-free endpoint/tolerance/alignment gates, unique visible native-edge
  resolution, `CreateAuxiliaryViewAt2` execution, persistent parent/arrow/label readback, and a
  rotation-based flip verifier that covers the SolidWorks 2025 `IView.FlipView` readback gap.
  Aligned/unflipped and detached/flipped cases passed save/close/read-only-reopen plus independent
  verification with stable normalized fingerprints and no `validation/` changes. Capability
  manifest 0.8.0 marks auxiliary views `supported/live`; because SolidWorks 2025 ignores
  `show_arrow=false` and exposes no visibility setter, hidden arrows and explicit auxiliary-label
  placement are explicitly capability-blocked before COM.
- Completed C4 with repository-native center marks, symmetry centerlines, and explicit detail and
  auxiliary-label placement. Auxiliary explicit labels retain the native projection arrow, clear
  its non-positionable text, and use a deterministic leaderless parent-view-owned native note with
  the same text format and an exact `IAnnotation.SetPosition2` coordinate. Strict readback covers
  owner, name, visibility, text, format, position, native arrow line, and native label anchor.
  Aligned/unflipped and detached/flipped SolidWorks 2025 SP5 cases passed in-memory,
  save/close/read-only-reopen, and independent verification with identical normalized fingerprints.
  Capability manifest 1.0.0 marks the complete C4 element set `supported/live`; hidden auxiliary
  arrows remain fail-closed because the native API ignores `show_arrow=false`. For center elements
  and detail labels, the C# preflight resolves frozen feature IDs to unique circular B-Rep edges, applies
  a document-level automatic-center-element policy, creates native single/linear/circular mark
  groups and horizontal/vertical attached centerlines, rejects style degradation and unplanned
  elements, and fingerprints exact geometry across save/close/read-only-reopen and independent
  verification. SolidWorks 2025 SP5 passed a two-hole linear group, both centerline axes, and an
  explicit detail-label case.
- Completed D1 with a repository-owned offline/integration/live ViewPlan validation matrix. Added immutable
  pre/post SHA-256 snapshots for the complete `validation/` tree, fresh-output enforcement,
  fail-closed lane ordering, per-case timeout/return-code/log hashes, and one normalized JSON
  report. Added a portable Roslyn x64 C# contract runner so local and CI checks execute the same
  45 private ViewPlan contracts, plus a repository-owned live runner that builds the production C#
  Execution Service and keeps Python free of COM calls. SolidWorks 2025 SP5 passed all 13 live
  cases across basic/projected, section, broken-out, detail, auxiliary, and center-element paths;
  each case passed in-memory, save/close/read-only-reopen, and independent fingerprint checks and
  produced a new drawing plus audit sidecar. The temporary service exited cleanly and all four
  `validation/` artifacts remained byte-identical. Hidden auxiliary arrows remain fail-closed.
- Completed D2 by making ViewPlan 1.4 the only drawing protocol on the default MCP surface. The
  FastMCP server is now version 2.0.0 and exposes six semantic tools; its instructions, checked-in
  semantic-tool contract, and Codex allow-list all default-exclude DrawingPlan 1.0 and the
  superseded bridge verbs. Native DrawingPlan 1.0 Python helpers, private C# transactions, and its
  Schema remain intact for the explicit D4 compatibility entry, while D3 cleanup remains separate.
- Completed D3 by deleting the transitional external executor bridge, its tests/exports, and the
  default MCP prompt that targeted an installed planning Skill. Production now uses the immutable
  repository-native `native-v3` 3.0.0 pack, prompt-pack contract 2.0, and prompt request/envelope
  3.0; the former `mcp_tools` mode, external tool allow-list, Skill discovery, PowerShell wrapper,
  and executor CLI subprocess path are absent from runtime code. The default server exposes six
  tools and zero prompts, while external Skills remain design references only.
- Completed D4 by moving the native DrawingPlan 1.0 validate/create/verify chain out of the default
  server into an explicit three-tool FastMCP compatibility entry. Its checked-in contract publishes
  the complete structured 1.0 Schema, create/verify route only to the existing private C#
  transactions, and the independent process resynchronizes execution state before one bounded
  retry. A dedicated PowerShell launcher is provided, but the compatibility server is excluded
  from the default Codex registration and never accepts or converts ViewPlan 1.4.
- Completed E0 release-candidate closure on the post-D4 codebase. The final repository-owned
  offline/integration/live matrix passed 6/6, including 13/13 SolidWorks 2025 SP5 persisted
  ViewPlan cases. Added a real stdio MCP DrawingPlan 1.0 smoke runner; its isolated three-tool
  server passed validate/create/verify with state `0 -> 1 -> 1`, generated a new drawing and audit
  sidecar, and preserved the complete `validation/` tree byte-for-byte. DrawingPlan create/verify
  transactions now use the same 180-second long-operation timeout as ViewPlan transactions, with
  focused timeout-routing coverage.
- Added repository-native Codex support through `.codex/config.toml`, a dedicated Codex launcher,
  MCP server instructions, and tool annotations.
- Changed drawing plan tool inputs from opaque JSON strings to structured `DrawingPlan` objects so
  MCP clients receive the complete strict schema during tool discovery.
- Integrated the installed `solidworks-plan-drawing-views` Skill without changing its workflow or
  references; prompt packs inject only supplemental policy against its exact schema-1.4 contract.
- Added the transitional `validate_frozen_view_plan` and `execute_frozen_view_plan` compatibility
  bridge during early migration, with unchanged-plan and no-overwrite checks. B4 superseded these
  registrations with the repository-native ViewPlan operations; D3 later removed the compatibility
  files after the native validation/execution matrix passed.

- Replaced the default 58-tool MCP boundary with a constrained engineering-semantic surface, now
  totaling nine tools after B4; preserved the former adapter as explicit `legacy_server.py`
  diagnostics.
- Added schema-1.0 frozen drawing plans with strict Pydantic and C# validation, localized standard
  view resolution, selection-scoped projected views, explicit style/configuration/scale handling,
  sheet-bound and overlap checks, same-directory transactional saves, close/read-only-reopen
  verification, atomic commit, SHA-256 audit sidecars, and read-only independent re-verification.
- Added a SolidWorks 2025 live validation plan covering base/projected views, persistent parentage,
  localized configuration names, custom scale, shaded-with-edges, and tangent-edge read-back.
- Forced the C# execution target to x64, made Interop locations configurable, bounded idempotency
  retention, replayed verified duplicate results, and disabled proxy inheritance for loopback HTTP.
- Added `knit_surfaces`: knit every sheet/surface body in the active part into one surface-knit
  feature, optionally forming a solid. `formed_solid` and the Verified flag report whether a solid
  ACTUALLY formed (checked by solid-body count delta, not the input request flag). Live-verified on
  both an open two-surface knit and a watertight six-surface shell that closes into a solid.
- Added `search_solidworks_references` for short, page-cited retrieval from locally owned SolidWorks PDFs.
- Added `scripts/build_reference_index.py`; PDFs stay external and generated page indexes remain under the gitignored `.solidpilot/` directory.
- Added eight SolidWorks Simulation tools: create/list/delete static and topology studies, add fixed
  fixtures and normal forces, mesh and solve, extract stress/displacement/factor-of-safety results,
  and configure topology goals, mass reduction, preserved faces, and minimum thickness.
- Added strict adapter-side validation for model-space face-coordinate arrays and a dedicated
  `SIMULATION_TIMEOUT` (600 seconds by default) for synchronous meshing and solves.
- Corrected Simulation COM interop for dispatch-array face selections, mesh length units and quality,
  result-array ordering/units, material-free user-yield FoS fallback, and topology edit transactions.

This file records changes made by the independently maintained fork after upstream commit
`a7348f0`. Upstream history remains available in Git.

## 2026-07-12 — Reference-modeling pass (design photos + user input)

### Added

- `load_reference_image` — normalize a design photo/drawing from disk (png/jpg/bmp/gif/tiff)
  and SEE it: optional normalized crop box to zoom into details/dimensions, long-edge cap,
  returned as MCP image content. Backed by a new pure-image `prepare_reference_image`
  execution handler (no COM, no state change).
- `capture_view_set(reference_image_path=...)` and `inspect_model(reference_image_path=...)` —
  compose the design photo as a labelled row ABOVE the live model views in one PNG: the
  compare-and-iterate loop for modeling from a reference, one image per check.
- `solidworks_help("reference_modeling")` — the photo-to-part protocol (decompose first, crop
  for details, one stated scale assumption, feature-tree plan, per-feature volume checks,
  reference-vs-model visual compare after every major feature).

### Changed

- 47 → 48 tools while the always-loaded schema SHRANK (~32.6k → ~31.4k chars): concise
  schema descriptions for auto_center_marks, open_document, add_hole_callout, ensure_ready,
  export_document, and set_part_material (full guidance stays in source docstrings and
  on-demand help).

## 2026-07-12 — Native assembly pass (first slice)

### Added

- `open_new_assembly`, creating a blank assembly from the default (or a given) template.
- `insert_component`, inserting saved parts/assemblies with verified rename, verified
  configuration, and tri-state `fixed` (omitted preserves SolidWorks' first-component grounding).
  XYZ placement is documented as approximate (bounding-box centre).
- `add_assembly_mate` — coincident / concentric / distance mates through the selection-free
  `IAssemblyDoc.CreateMateData`/`CreateMate` path. Faces are addressed by Base64 persistent
  references (cross-call safe) or component + same-call face index (top-level only; a lightweight
  component involved via the index path is resolved individually, never the whole assembly).
- `analyze_assembly` — read-only structure: components with suppression/fixed/configuration/
  position, per-face persistent `ref` handles (`include_faces=true`), and mates read from the
  MateGroup subfeatures (feature name, type, alignment, suppression, dimension value).
- `inspect_model` now detects assemblies: component/mate structure plus mass instead of
  part-only geometry/features analysis; montage capture unchanged.

### Changed

- Increased the MCP surface from 43 to 47 tools.
- Every response now reports the live document type (PART/ASSEMBLY/DRAWING) instead of a
  hardcoded PART.

## 2026-07-12 — Performance and visual-assignment pass

### Added

- `execute_batch`, running up to 100 ordered execution-layer operations in one MCP call with
  compact results and references to earlier outputs.
- `capture_view_set`, returning a labelled 1–4 view PNG montage.
- `inspect_model`, combining compact topology, mass, feature-tree summary, and a multi-view image.
- `solidworks_help`, moving detailed workflow guidance behind on-demand topics.
- Native `extrude_feature(feature_type="revolve_cut")` support.

### Changed

- Increased the MCP surface from 39 to 43 tools.
- Reused persistent localhost HTTP connections instead of creating one client per CAD operation.
- Reduced the always-loaded MCP schema from about 54,243 to under 30,000 characters while retaining
  detailed source docstrings and on-demand help.
- Documented a measurement-first screenshot-to-CAD workflow using synchronized orthographic views.

### Verification

- Added adapter orchestration tests, contract coverage for all 43 tools, live montage inspection,
  live revolved-cut verification, and before/after latency and schema-size measurements.

## 2026-07-11 — Fork improvement pass

### Added

- `add_sketch_text`, providing native SolidWorks sketch-text creation with anchor, height,
  rotation, font, bold, and italic controls.
- `capture_view`, providing named-view, zoom-fit PNG capture returned directly as MCP image content.
- Hash-pinned Windows/Python 3.12 runtime and development dependency lock files.
- Windows CI for contract checks, offline compiler tests, and Python compilation.
- Public fork attribution, DCO contribution terms, environment template, and security guidance.

### Changed

- Increased the MCP surface from 37 to 39 tools.
- Replaced verbose pipe-delimited adapter results with compact JSON responses.
- Preserved additional mass-property precision so small feature volume changes remain visible.
- Targeted .NET Framework 4.8.1 in the execution project.

### Fixed

- Retried boss creation with all sketch regions selected, allowing disjoint profiles such as
  dissolved TrueType glyphs to produce one joined boss.
- Passed SolidWorks sketch-text width and spacing as percentage integers, preventing collapsed,
  self-intersecting glyphs.
- Added actionable diagnostics for blind cuts whose reference plane is coincident with a model
  face.
- Verified Right Plane blind cuts as single-direction: the default cuts toward positive X and
  `reverse=true` flips the direction.

### Verification

- Live verification was performed against SolidWorks 2026 for raised sketch text, direct PNG
  capture, multi-region boss creation, cut direction, and the offset-plane blind-cut workaround.

## Fork base — 2026-07-11

- Forked from `eyfel/mcp-server-solidworks` at commit `a7348f0`.
- Fork modifications maintained by Benny Cohen beginning 2026-07-11.
