---
name: solidworks-finalize-drawing-layout
description: Finalize an independently verified, dimensioned single-part SolidWorks drawing through repository-native DrawingLayoutPlan 1.0 planning, deterministic publication, transactional creation, and read-only verification. Use only after the dimension Skill returns its immutable DimensionPlanningRequest, published DimensionPlan, new .SLDDRW, and verification sidecar.
---

# SolidWorks Finalize Drawing Layout

Create exactly one new final drawing from the verified dimension-stage drawing. Keep the complete source DimensionPlanningRequest embedded unchanged in the layout request so this stage cannot return to the view baseline or bypass dimension verification.

## Allowed semantic tools

- `solidworks_status`
- `initialize_part_drawing_layout_handoff`
- `publish_validated_part_drawing_layout_plan`
- `validate_part_drawing_layout_plan`
- `create_final_part_drawing`
- `verify_final_part_drawing`

Do not call private executor verbs, raw HTTP, COM, UI automation, legacy DrawingPlan tools, or a second MCP client.

## Immutable inputs

Require all of the following from the completed dimension stage:

- the exact `DimensionPlanningRequest` returned by `initialize_part_drawing_dimension_handoff`;
- its published `dimension_plan.json`;
- the new dimensioned `.SLDDRW` created from that plan;
- the matching independent dimension verification sidecar;
- a new layout publication directory and a new final `.SLDDRW` output path.

Never reconstruct, shorten, repair, or substitute the dimension request. Do not accept a ViewPlan drawing, view verification sidecar, or unverified dimension drawing as a replacement.

## Workflow

1. Call `solidworks_status` only when readiness is unknown.
2. Call `initialize_part_drawing_layout_handoff` with the exact dimension request and all three matching dimension artifacts. The tool revalidates request-to-plan continuity and the C# initializer independently binds the verified drawing. Stop on `blocked`, `FAILED`, or any hash/integrity mismatch.
3. Read the complete immutable `drawing-layout-handoff.json`, `drawing_layout_planner/contracts/drawing-layout-planning-request.schema.json`, `drawing_layout_planner/contracts/drawing-layout-plan.schema.json`, current layout capability manifests, and `drawing_layout_planner/prompt_packs/native-v1/{manifest.json,system.md,task.md}`.
4. Construct exactly one complete `LayoutPlanningRequest` from the returned `planning_request_context`. Copy `source_dimension_request`, `handoff`, and `publication_directory` byte-for-byte in meaning. Add one request ID, one plan ID, one timestamp, explicit authorization, layout intents, and evidence-backed assumptions. Never authorize sheet, scale, view, or format changes without explicit approval.
5. Call `publish_validated_part_drawing_layout_plan` once with that request. The repository solver creates and atomically publishes the only plan. Keep the returned request hash and published plan unchanged. A `capability_blocked` plan is valid evidence; stop before creation.
6. Load the exact published `drawing_layout_plan.json` and call `validate_part_drawing_layout_plan` with the same request and new output path. Stop unless the result is `VALID`, the executor accepts it, and execution readiness is `supported`.
7. Call `create_final_part_drawing` once with the same plan, request, and output path. Do not retry with a modified plan or overwrite an upstream drawing.
8. Call `verify_final_part_drawing` with the identical plan, request, and output path. Report success only when independent read-only verification returns `COMPLETED` and `verified=true`.

## Evidence and stop rules

- Use exact handoff object IDs, view names, bounds, locked zones, spacing, dimension IDs, and source hashes. Screen pixels and screenshots are not collision or semantic evidence.
- The deterministic solver owns coordinates and phase order. The Skill supplies intent and authorization, not native mutation steps.
- Preserve dimension count, values, attachments, text, tolerances, view semantics, projection, configuration, display state, section definitions, model association, and frozen geometry.
- Never delete objects or add manufacturing annotations during final layout.
- If any required boundary or execution capability is not live-supported, retain the published plan and explain the blocker. Never simplify, downgrade, republish, or call create.
