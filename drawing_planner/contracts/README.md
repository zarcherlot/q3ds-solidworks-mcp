# Drawing-planner contracts

`view-plan.schema.json` is the repository-owned runtime authority for the
`solidworks-view-plan` 1.4 protocol. Its initial contents were imported byte-for-byte from the
documented design source identified by `view-plan.contract.json`; production code must not discover
or read a user-installed Skill schema.

Changing the schema requires:

1. an explicit protocol-version decision;
2. updating the contract lock SHA-256;
3. updating the prompt compiler, validators, capability manifest and C# reader together;
4. passing all schema fixtures and persisted execution/readback tests.

The source Skill remains reference material only and is never modified by this repository.

`prompt-pack.schema.json` 2.0 and `prompt-request.schema.json` 3.0 define the repository-native
PlannerEngine prompt boundary. The production `native-v4` pack returns one schema-constrained
candidate only; it contains no external Skill discovery, CLI execution mode, or execution-tool
allow-list. Released historical packs are not rewritten and are not production-selectable.

`planning-request.schema.json` and `planning-result.schema.json` publish the strict semantic
PlannerEngine boundary. Their structure is generated from `PlanningRequest` and `PlanningResult`;
contract tests compare the committed Draft 2020-12 documents with the Pydantic schemas to prevent
silent drift. Cross-field state rules remain enforced by the domain-model validators.

Candidate plans pass through `RepositoryViewPlanValidator` in the fixed order integrity, Draft
2020-12 Schema, semantics, feature coverage, and sheet layout. Integrity or Schema failure prevents
dependent gates from running. The accepted producer/ruleset identity is recomputed from the selected
repository planner profile and immutable prompt pack; it is not trusted from model output.

`feature-taxonomy.schema.json` 1.0 defines the experimental repository-owned vocabulary contract
for semantic mechanical features and expression-requirement kinds. Versioned artifacts live under
`drawing_planner/taxonomies/`; production does not discover or accept runtime taxonomies. The
initial `mechanical-features-1.0.0-experimental.json` artifact is vocabulary and initializer-planning
scaffolding only. It does not change ViewPlan 1.4 or claim execution support.

`view-plan-1.5.schema.json` starts the explicit M2 protocol successor. It replaces the overlapping
`required_mode` / `expression_mode` pair with `requirement_kind` / `expression_method`, changes
single-view satisfaction to a non-empty primary/supporting view set, and freezes required
independent-projection counts plus expected opening, occurrence, depth, and semantic-relation
evidence. `ViewPlan15SchemaValidator` and `ViewPlan15ExpressionValidator` are offline experimental
gates only. ViewPlan 1.5 is not registered on the default MCP surface, accepted by the capability
manifest, compiled by C#, or executable; ViewPlan 1.4 remains the unchanged runtime authority until
the remaining M2 migration gates are implemented and verified together.
