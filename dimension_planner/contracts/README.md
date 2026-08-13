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

F2 still exposes no Agent-visible dimension tool and performs no SolidWorks mutation. Deterministic
engineering validation belongs to F3; native creation and persisted readback belong to F4-F5.
