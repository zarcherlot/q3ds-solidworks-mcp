# Codex repository guidance

## Architecture

- Use `adapters/codex/server.py` as the Codex entry point. It delegates to the shared semantic MCP.
- Keep the default MCP surface limited to the engineering-semantic tools in
  `adapters/claude/server.py`; never expose C# executor operations or legacy COM-shaped verbs.
- Keep SolidWorks COM calls in the C# execution service. Python may validate, normalize, compile
  prompts, and orchestrate semantic transactions, but must not become a second COM layer.

## Drawing planning

- Follow `docs/VIEW_PLANNING_INTEGRATION_PLAN.md`. The production target is the repository-owned
  PlannerEngine and the existing repository C# Execution Service.
- Treat `$solidworks-plan-drawing-views` as a design/reference source, not a production runtime
  dependency. Do not modify its `SKILL.md` workflow or its `references/` guidance.
- Do not add features to the temporary external `executor_bridge.py` path. Remove that path only
  after the repository validators, PlannerEngine, semantic MCP tools, and C# transaction satisfy
  their documented acceptance gates.
- Store tunable prompt text only in immutable versioned packs under
  `drawing_planner/prompt_packs/`; core policy and output contracts are repository-owned and cannot
  be overridden by a pack or runtime data.
- Do not translate schema-1.4 plans into the legacy DrawingPlan 1.0 tool chain. Unsupported views,
  center elements, frozen geometry, integrity checks, or layout constraints must fail explicitly.
- Keep engineering plan validity separate from execution capability. A valid plan may be published
  as `capability_blocked`, but the create transaction must reject it until every required capability
  is marked `supported` with live readback evidence.
- The legacy `validate/create/verify_part_drawing` chain remains available only for native
  DrawingPlan 1.0 callers.

## Verification

- Run `python -m pytest -q adapters/claude/tests drawing_planner/tests` after Python, schema, MCP,
  or prompt changes.
- Run `python solidworks-compiler/pycompiler/tests/test_compiler.py` after compiler changes.
- Run `python -m compileall -q adapters drawing_planner solidworks-compiler scripts` before handoff.
