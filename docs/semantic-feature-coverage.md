# Semantic Feature Coverage

Status: experimental development specification

Branch: `experiment/semantic-feature-coverage`

Production status: not releasable

## 1. Objective

Establish a repository-owned, versioned mechanical-feature vocabulary and a closed-set coverage
contract for single-part drawing-view planning. The feature shall make it possible to prove that
every manufacturing-, assembly-, function-, or inspection-significant semantic feature in a frozen
initializer handoff has an evidence-backed expression requirement and is covered by an appropriate
view or by an explicit controlled exemption.

This feature does not add dimensions, tolerances, datums, surface finish, material state, heat
treatment, title-block content, or export behavior. It must preserve the separation between
engineering validity and current C# execution capability.

## 2. Architectural constraints

- SolidWorks COM access remains in the repository C# execution service. Python may validate,
  normalize, compile prompts, and orchestrate semantic transactions only.
- `adapters/codex/server.py` remains the Codex entry point and the default MCP surface remains
  engineering-semantic.
- The repository-owned PlannerEngine, validators, contracts, prompt packs, capability registry,
  and C# transaction are the production path.
- Schema-1.4 plans must not be translated into DrawingPlan 1.0. An incompatible protocol revision
  requires an explicit version decision and side-by-side validation.
- A valid plan may be published as `capability_blocked`, but creation must fail before COM until
  every required capability has supported/live evidence.
- Tunable text belongs in immutable prompt packs. Taxonomy, completeness policy, identity rules,
  and output contracts are repository-owned and cannot be overridden by a pack or runtime data.

## 3. Domain model

### 3.1 Separate identities

The implementation shall distinguish:

- `feature_id`: stable semantic feature identity, such as `FT-HOLE-003`;
- `feature_class`: code from the versioned taxonomy, such as `hole.blind.drilled`;
- `source_feature_ref`: optional SolidWorks feature-tree reference;
- `geometry_refs`: frozen B-Rep face, edge, and vertex evidence;
- `occurrence_ids`: individual instances belonging to a repeated or patterned feature.

A B-Rep face is not a semantic feature. One semantic feature may own several faces and edges, and a
compound feature may contain child features.

### 3.2 Classification axes

The taxonomy shall keep these concepts distinct:

1. global shape;
2. geometric feature;
3. specialized structure;
4. relation between features;
5. manufacturing or stock context.

This prevents holes, symmetry, sheet metal, and cast stock from being treated as peers in a single
ambiguous list.

### 3.3 Expression requirements

`requirement_kind` states what information must be expressed:

- `opening_and_count`;
- `shape_true_form`;
- `internal_profile`;
- `depth_extent`;
- `axis_direction`;
- `angular_orientation`;
- `location_relation`;
- `pattern_relation`;
- `transition_detail`.

`expression_method` separately states how it is expressed:

- direct or projected view;
- section, removed section, or broken-out section;
- detail or auxiliary view;
- multiple independent views.

One requirement may be satisfied by multiple views. The validator must reject parallel or
information-equivalent views when independent projections are required.

### 3.4 Derivation provenance

Every requirement shall identify one of:

- `deterministic_geometry`;
- `model_feature_data`;
- `model_pmi`;
- `controlled_user_requirement`;
- `planner_engineering_decision`.

Geometry claims must be recalculable. PMI and user requirements must be hash-bound. Planner
judgement must state evidence and omission impact and must never be represented as a geometric
fact. Insufficient evidence produces a structured open question, not a passing requirement.

## 4. Required development

### M1 — contracts, identity, and closed-set completeness

- Add an immutable, versioned `feature-taxonomy` JSON Schema and mechanical taxonomy artifact.
- Reject unknown feature classes and validate labels, definitions, scope, evidence needs, default
  requirements, source status, and implementation status.
- Add a strict Python loader that validates uniqueness, parent existence, hierarchy acyclicity, and
  complete requirement/derivation namespaces.
- Define `model-semantic-features.json`, produced from the C# initializer path and hash-bound into
  the immutable handoff.
- Extract or record semantic feature IDs, class, feature-tree source, B-Rep evidence, axes, normals,
  openings, extents, hierarchy, relations, occurrences, patterns, and unresolved facts.
- Add `required_feature_ids` and controlled exemptions.
- Enforce closed-set equality between required features and covered or exempted features.
- Reject missing, invented, duplicated, unclassified, or unjustifiably exempted features.

### M2 — expression contract and deterministic validators

- Replace the overlapping `required_mode`/`expression_mode` semantics in the next explicitly
  versioned ViewPlan contract with `requirement_kind` and `expression_method`.
- Change single `satisfied_by` references to non-empty view sets with optional primary/supporting
  roles and minimum-independent-projection constraints.
- Implement true-shape checks using frozen face normals or feature axes and view directions.
- Extend section validation for axis intersection, offset paths, depth extent, local boundaries,
  symmetry preconditions, and rib/web longitudinal-section rules.
- Validate opening and instance counts from semantic groups, including suppressed pattern members.
- Validate depth and axial hierarchy from start/end faces and effective/total depths.
- Validate spatial direction using independent projections and auxiliary-view geometry.
- Validate pattern, mirror, symmetry, coaxial, and intersection relations.
- Add scale/line-width-based projected discernibility checks without forcing details for ordinary
  non-critical rounds or chamfers.

### M3 — taxonomy coverage and production prompt integration

- Resolve existing overlaps: obround slots versus holes, ribs in duplicate topics, patterns versus
  symmetry, cast versus forged context, and overall shape versus local features.
- Add or explicitly exclude springs, conduits and internal flow paths, weld preparations, worms,
  racks, sprockets, pulleys, timing teeth, cams, retaining-ring grooves, lubrication grooves,
  wrench flats, polygonal ends, forged and molded stock, compound/deep hole systems, and local
  sheet-metal forms.
- Replace “every face requires a manufacturing definition” with semantic-feature significance.
- Preserve model-tree order only as provenance; do not treat it as manufacturing or inspection
  order.
- Make production planning consume repository-controlled taxonomy-derived rules. Debug-only
  Markdown routing is not sufficient production implementation.
- Record the actually used taxonomy, ruleset, prompt pack, schema, and handoff hashes in provenance.

### M4 — execution evidence and release closure

- Extend C# extraction/readback for FeatureData, semantic face groups, hole levels, pattern seeds
  and occurrences, suppression, mirror/symmetry references, PMI/datums, and necessary sketch axes.
- Extend transaction and independent readback evidence for view sets, projection directions,
  section geometry, feature-axis intersections, true-shape directions, center elements, scale, and
  stable fingerprints.
- Extend the versioned capability registry to assess expression validation and semantic resolution
  independently from native view creation.
- Preserve fail-closed `capability_blocked` behavior for any missing exact creation or readback
  capability.
- Add handbook/standard provenance with source, chapter or standard number, adopted version,
  applicability, and authority level: mandatory standard, project rule, company rule, handbook
  recommendation, or engineering guidance.

## 5. Engineering judgement boundaries

Pure geometry must not invent:

- functional or process datums;
- the design significance of a local transition;
- acceptance tolerances or manufacturing requirements;
- an angular zero for rotationally symmetric geometry;
- a unique minimum view set when multiple engineering-valid solutions remain.

Those decisions require model PMI, controlled requirements, or an evidence-backed Planner decision.
Validators verify the frozen decision; they do not silently replace it with heuristics.

## 6. Test and release policy

This branch is experimental. Nothing introduced by this feature may be advertised as production
supported until all applicable gates pass.

Required test layers:

1. taxonomy Schema and strict-loader contract tests;
2. semantic extraction fixtures for simple, compound, patterned, mirrored, suppressed, oblique,
   intersecting, thin-wall, and free-form cases;
3. negative closed-set tests for omissions, inventions, duplicates, invalid exemptions, and B-Rep
   IDs used as semantic IDs;
4. expression-rule tests for wrong true-shape direction, missed axes, insufficient depth, dependent
   multiview projections, wrong pattern counts, missing angular baselines, and discernibility;
5. integration tests across initializer, PlannerEngine, validators, capability assessment, and C#
   preflight;
6. SolidWorks live tests covering in-memory readback, save/close, read-only reopen, independent
   verification, source-model cleanliness, stable fingerprints, and no changes to `validation/`.

Repository baseline commands after relevant changes:

```powershell
python -m pytest -q adapters/claude/tests drawing_planner/tests
python solidworks-compiler/pycompiler/tests/test_compiler.py
python -m compileall -q adapters drawing_planner solidworks-compiler scripts
```

Release additionally requires:

- every newly supported capability marked `supported/live` only after recorded live evidence;
- all offline, integration, and applicable live matrices passing from a clean output directory;
- no unexplained warnings, skipped mandatory cases, source-model mutations, or validation-fixture
  mutations;
- an explicit protocol-version and migration decision;
- review of mechanical-handbook/standard provenance;
- a release-candidate report that identifies evidence paths and reproducible commands.

## 7. Initial implementation slice

The first experimental slice delivers only the taxonomy Schema, a versioned initial controlled
vocabulary, a strict read-only loader, and offline contract tests. It intentionally does not mutate
ViewPlan 1.4, change the default MCP surface, or claim initializer/C# execution support.

## 8. Validation dataset record

The read-only validation source supplied for this experiment is:

```text
C:\Users\zarch\Desktop\solidwokrs-mcp-test
```

The current source contains five native single-part models. Their observed SHA-256 bindings are:

| Model | SHA-256 | M1 use |
|---|---|---|
| `ACCCMD-01-010102-0100-PT 殷钢固定板.SLDPRT` | `0bd799510f00a0ad855167f89501bdeff264faa854adb357e93207a3c8c27538` | semantic identity and six-hole/slot reference case |
| `ACCCMD-01-010104-0300-PT C型夹-120滚针底板双孔型.SLDPRT` | `dd26951ed19d39ddf62404ca586cc09349d1ea7cf1200efa0443b72ddd318754` | future bracket and compound-hole case |
| `ACCCMD-01-010107-0300-PT 真空束管支撑板.SLDPRT` | `eb9651163f7649cb6fc3cb0fde2bfd03c0dd2a6e4b3a9c4f0db0e5fd0b916cf8` | future support-plate and conduit case |
| `ACCCMD-01-010109-0200-PT 固定环.SLDPRT` | `a88206575e16a0affbb9f9c87c153664386b5331938d34e246abfab3d4ed4792` | future ring/groove case |
| `ACCCMD-12\12345.SLDPRT` | `f6dd764dd4bfff436e86521a30652d8fdb53735eebb1cf432582bcf27bbf7f0a` | future controlled-special-feature case |

At the time of this M1 run, the temporary `.drawing-handoff-*` directories under the first model
were removed by an external host workflow while the native models remained. They were inspected
read-only before removal; no repository test depends on those transient paths. The captured fixed
plate evidence showed B-Rep IDs such as `B0F8`–`B0F13` being used as six separate coverage IDs for
one repeated hole family. M1 therefore treats those IDs only as `geometry_refs`, and represents the
semantic hole and its actual/suppressed occurrences separately.

## 9. M1 initializer integration status (2026-08-12)

Implemented on this experimental branch:

- `initialize_part_drawing_handoff` accepts an explicit `semantic_feature_profile` with default
  `none`; production handoff 1.0 remains compatible unless `m1-experimental` is requested.
- The C# initializer stages `model-semantic-features.json` and the exact experimental taxonomy,
  binds both into the manifest by absolute path and SHA-256, publishes them before the manifest,
  and includes them in rollback/no-overwrite handling.
- The extractor now records body-level identities plus repository-controlled typed FeatureData for
  Hole Wizard/simple holes, circular-profile extruded cuts, bosses/cuts, ribs, transitions, shells,
  threads, and sheet-metal forms. It binds each accepted feature to the exact frozen B-Rep faces
  and edges using COM identity; feature names and rendered geometry never select a class.
- Circular-profile cut classification is fail-closed: only a cut whose parent `ProfileFeature`
  contains one or more complete circles and no other non-construction geometry is promoted to a
  hole class. Mixed/non-circular profiles remain pockets. Hole Wizard classes and opening counts
  come from typed FeatureData.
- Pattern and mirror seeds are resolved through typed FeatureData. A relation is published only
  when its required axis or mirror-plane normal is natively readable. Selection-backed axis/plane
  geometry is frozen before `ReleaseSelectionAccess`; linear patterns may also derive their exact
  axis from the native translation difference between `GetTransform(0)` and `GetTransform(1)`.
  Seed identity resolution is COM-based and depth-aware: it stops at the first unique
  owner/parent/child lineage level and rejects same-level ambiguity. Circular patterns may derive
  their axis from the native rotation transform, and occurrence binding applies every frozen
  instance transform to the seed cylinder axis/origin before requiring a unique B-Rep match.
- The artifact deliberately remains `status=incomplete`, with no `required_feature_ids`, until
  controlled functional/manufacturing/inspection significance is supplied. The initializer may
  still publish `model_evidence_status=exhausted` when it has exhausted what the `.SLDPRT` can
  prove; unresolved controlled meaning is reported separately as
  `controlled_semantics_status=unresolved` and a structured `optional_controlled_input` question.
- Python strictly validates the manifest bindings and reloads the semantic artifact, checking model,
  geometry-report, taxonomy, class namespace, graph, and B-Rep identities. The verified semantic
  pair is attached to Planner prompts as two additional immutable artifacts. Incomplete semantics
  remains a valid handoff input for geometric reasoning, but the production coverage gate rejects
  it with `VP-COVERAGE-SEMANTIC-UNRESOLVED`; an empty required-feature set cannot pass closed-set
  coverage.
- Offline tests cover production compatibility, experimental loading, paired bindings, tampering,
  closed-set failures, and strict schemas.

Live validation completed in the isolated worktree:

- `outputs/m1-five-models-experimental-20260812-r6` is the current r18 five-model evidence set.
  All five initializer transactions returned `COMPLETED`, `verified=true`, and
  `handoff_integrity=pass`; independent semantic/taxonomy/geometry/image hash checks passed. It
  extracts 32 typed local features, 9 exact occurrences, 5 relations, 3 through-hole extents, and
  4 independently B-Rep-bound cosmetic threads. All artifacts correctly remain `incomplete`.
- `outputs/m1-five-models-experimental-20260812-r5` contains the final five independent initializer
  transactions from the earlier runtime r11. All returned `COMPLETED`, `verified=true`, and
  `handoff_integrity=pass`; all source, semantic-artifact, and taxonomy hashes revalidated.
- That matrix extracted 28 typed local features, froze 10 occurrences, one pattern relation, and
  three through-hole axial extents across the five models while preserving all five source hashes.
  The artifacts correctly remained incomplete with one or two controlled questions each.
- `outputs/m1-occurrence-validation-20260812-r1` proves the fixed-plate six-hole group has
  `opening_count=6`, six distinct face bindings, twelve distinct edge bindings, and one native-axis
  pattern relation. `outputs/m1-through-depth-validation-20260812-r1` proves the three M8 through
  holes have frozen start/end projections `0.000/0.010 m` and matching effective/total depth.
- `outputs/m1-pattern-validation-20260812-r1` records the r9 linear-pattern lifecycle fix and
  native-transform fallback. It publishes a validated `relation.pattern` whose member is the
  circular-profile hole feature and whose frozen axis is `[0, 0, 1]`; the remaining pattern gap is
  exact actual/suppressed occurrence geometry.
- `outputs/m1-depth-aware-lineage-validation-20260812-r1` records the identity-lineage and
  circular-transform implementation. Model 2 now publishes both its linear-pattern and mirror
  relations; model 3 publishes its mirror relation. Model 4 freezes the circular-pattern axis and
  all three rotated M16 hole occurrences with unique face/edge bindings. Follow-up
  `outputs/m1-thread-edge-validation-20260812-r1` proves cosmetic threads bind independently via
  their typed edge selection (model 3: `B0F14/F15/F16`; model 4: `B0F13`). The focused
  transactions all returned `COMPLETED`, `verified=true`, and `handoff_integrity=pass`.

M1 final live gate result (r20):

- Extend exact occurrence extraction to remaining ICE/history, non-cylindrical linear-pattern,
  and mirror cases. ICE rows still have no unique current owner/child B-Rep on the observed models;
  they remain omitted instead of being guessed. Circular cylindrical occurrence extraction is now
  proven on model 4. Mirror relations and normals are proven, but occurrence publication additionally
  requires complete topologically matching seed/mirror face sets; both observed models correctly
  fail that stricter gate.
  The occurrence contract explicitly permits an empty B-Rep binding only for suppressed instances;
  every unsuppressed instance must bind frozen body geometry.
- Hole Wizard, simple-hole, and circular extruded-cut FeatureData is frozen separately as
  `hole_specification`. It records source specification such as type/end-condition, nominal depth,
  thread and compound-hole parameters without pretending that FeatureData depth proves B-Rep
  start/end geometry. The fresh-MCP r20 matrix at
  `outputs/m1-hole-spec-validation-20260812-r2` completed models 3/4/5 with verified immutable
  handoffs and unchanged source hashes. Models 3/4 prove typed Hole Wizard M8/M16 getters; model 5
  proves circular extruded-cut through/blind end conditions and nominal depths. This getter set is
  therefore promoted from implementation evidence to live proof.
- Controlled PMI/significance is optional external input, not a prerequisite for completing the M1
  software implementation. When absent, `required_feature_ids` remains empty and the unresolved
  significance question stays open. Such artifacts may be consumed by PlannerEngine for explicitly
  uncertain geometric reasoning, but must fail closed-set coverage and must never be advertised as
  semantic-complete.

The remaining omitted ICE/history and mirror occurrences are fail-closed extraction limits, not
unverified claimed support. With the r20 matrix and final repository regression (`166 passed`, 7
subtests; compiler `36/36`; C# `48/48`; compileall and `git diff --check` pass), the M1 software and
HoleSpecification live gates are closed and M2 expression-contract work may proceed.

## 10. M2 expression-contract initial slice (2026-08-12)

- The explicit migration target is `solidworks-view-plan` 1.5; frozen ViewPlan 1.4 and its default
  MCP/C#/capability surface remain unchanged and executable only under their existing contract.
- `drawing_planner/contracts/view-plan-1.5.schema.json` replaces overlapping modes with
  `requirement_kind` and `expression_method`, requires one primary plus optional supporting views,
  and freezes the minimum independent-projection count.
- The 1.5 requirement also freezes expected opening/unsuppressed-occurrence counts, B-Rep
  effective/total depths, and semantic relation IDs. The Schema rejects legacy fields and fields
  used with the wrong requirement kind.
- The offline deterministic expression validator rejects missing/duplicate view references,
  dependent antiparallel projections, incompatible view methods, wrong true-shape directions,
  missing section-feature bindings, absent or mismatched opening/occurrence/depth evidence, and
  nonexistent, incompatible, or non-member pattern/mirror/coaxial relations.
- Spatial-direction declarations must match a frozen feature/relation axis or plane normal. Section
  expressions additionally enforce feature-axis/path intersection, bounded depth coverage,
  symmetry evidence for half sections, and the rib/web longitudinal-section rule.
- Critical projected discernibility is calculated from frozen feature edges, view direction, view
  scale, line width, and a frozen minimum line-width ratio. The check is opt-in and therefore does
  not force detail views for ordinary non-critical rounds or chamfers.
- This slice intentionally publishes no semantic MCP tool and claims no 1.5 execution capability.
  Local/broken-out boundary evidence, full pipeline/PlanStore, C# contract/readback, MCP migration,
  and live matrices remain open M2 work.
- The focused M2 contract suite passes `12/12`. The repository regression after this slice is
  Python `178 passed, 7 subtests passed`, compiler `36/36`, C# ViewPlan 1.4 contract tests `48/48`,
  with compileall and `git diff --check` passing. This is regression evidence, not a claim that the
  remaining 1.5 execution migration is complete.
