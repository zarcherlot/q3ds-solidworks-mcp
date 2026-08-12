Plan views and sheet layout from the initializer's complete artifact handoff.

Upstream artifact paths:

{{UPSTREAM_ARTIFACTS_JSON}}

Frozen plan publication target:

{{VIEW_PLAN_TARGET_JSON}}

User requirements JSON:

{{USER_REQUIREMENTS_JSON}}

Apply the repository workflow policy and response contract to choose the main orientation, minimum
view set, view types, scale, feature coverage, frozen geometry, center elements, dimension zones,
and collision-free layout. Preserve every input path and SHA-256 binding required by schema 1.4.

When `output_mode` is `json_schema`, return only the schema-1.4 JSON object. When it is `mcp_tools`,
publish the complete object atomically at the specified `view_plan.json` target and end the planning
stage. The outer workflow may then call `validate_frozen_view_plan` and, only on explicit user write
intent, `execute_frozen_view_plan`. Neither call may repair, reinterpret, or downgrade the plan.
