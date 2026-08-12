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
