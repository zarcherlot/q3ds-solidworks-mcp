# H0 five-Skill release candidate

Status: preparatory readiness gate implemented; production live chain blocked by incomplete F7
capability promotion.

## Release boundary

H0 is the final production-only gate for this chain:

`bootstrap-solidworks-host` -> `solidworks-initialize-drawing-handoff` ->
`solidworks-create-drawing-views` -> `solidworks-dimension-drawing` ->
`solidworks-finalize-drawing-layout`.

Qualification transactions are not accepted as substitutes. The H0 run must use
`create_dimensioned_part_drawing` and `create_final_part_drawing`, then independently verify each
new drawing through their production semantic tools. Every tool call remains on the 24-tool
engineering-semantic MCP surface; private executor routes and COM operations remain internal to the
C# Execution Service.

## COM-free readiness gate

`release_candidate.h0_readiness` and `scripts/check_h0_release_readiness.py` establish the first H0
gate. The audit is read-only and does not start SolidWorks. It validates and SHA-256 inventories:

- the five-Skill order and each exact semantic allow-list;
- the contract/config/schema 24-tool surface, actual FastMCP discovery, and zero prompts;
- the ViewPlan 1.4, DimensionPlan 1.0 and DrawingLayoutPlan 1.0 Schemas;
- the ViewPlan, dimension, layout-boundary and layout-plan capability manifests;
- the G0-to-G7 manifest hash binding; and
- the exact Git commit and clean-worktree condition required for final evidence.

The report uses the strict `release_candidate/contracts/h0-readiness.schema.json` contract and is
published once. Exit code `0` means ready, `2` means a valid blocked report was published, and `1`
means the audit itself failed.

```powershell
New-Item -ItemType Directory -Path C:\temp\solidpilot-h0-r1
.\.venv\Scripts\python.exe .\scripts\check_h0_release_readiness.py `
  --repository-root . `
  --output C:\temp\solidpilot-h0-r1\h0-readiness.json
```

## Current blocker

The current dimension capability registry is version `0.3.0`. All 18 DimensionPlan kinds and the
six production execution elements required by F7 remain `planned`; only matrix-bound qualification
transactions may admit them. Consequently every production DimensionPlan remains
`capability_blocked`, and H0 cannot legally call `create_dimensioned_part_drawing`.

This agrees with the F7 section of the integration plan: the existing five-real-part matrix covers
only linear/diameter dimensions and does not satisfy the complete 18-kind promotion policy. G7 is
already promoted, but it cannot compensate for an unpromoted predecessor. The next H0 live step is
therefore gated on completing F7 evidence and promoting the reviewed candidate into
`dimension_planner/capabilities/current.json`.

After that promotion, a new immutable H0 live request/runner will consume one source model and
template, produce one plan and one new successor drawing per planning stage, verify every request
and SHA-256 link, close/read-only-reopen the final drawing, and freeze the final commit, runtime,
plans, drawings and sidecars in the release report.
