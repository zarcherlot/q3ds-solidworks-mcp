# Dimension planning contracts

F0 currently owns two research contracts:

- `dimension-api-probe.schema.json` freezes the complete SolidWorks 2025 SP5 probe request.
- `dimension-api-evidence.schema.json` validates immutable native-API evidence.

They are not DimensionPlan production contracts and must not be registered as Agent-visible MCP
tools. DimensionPlan 1.0 request/result/executor contracts are introduced separately in F2.

F1 adds two production handoff contracts without introducing DimensionPlan itself:

- `dimension-planning-handoff-request.schema.json` binds one independently verified ViewPlan
  drawing and any explicitly approved user inputs.
- `dimension-planning-handoff.schema.json` freezes the read-only native geometry, dimension,
  PMI, feature, annotation, provenance, and immutability readback published manifest-last.

The F1 endpoint remains private to the repository execution service until the semantic initializer
tool and `solidworks-dimension-drawing` Skill are introduced in F6.
