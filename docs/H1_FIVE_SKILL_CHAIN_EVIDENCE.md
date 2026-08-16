# H1 five-Skill production-chain evidence

Status: strict COM-free evidence contract and immutable publisher implemented; real H1 evidence is
blocked until F7 production capabilities are promoted.

## Purpose

H1 verifies one completed production run of the exact five-Skill chain. It does not call
SolidWorks and cannot turn qualification output into production evidence. The validator consumes
the response JSON saved after every semantic MCP call plus the actual plans, drawings, handoffs,
verification sidecars, runtime, source model and template.

The fixed stage order is:

1. `bootstrap-solidworks-host`
2. `solidworks-initialize-drawing-handoff`
3. `solidworks-create-drawing-views`
4. `solidworks-dimension-drawing`
5. `solidworks-finalize-drawing-layout`

The view, dimension and layout stages must use their production publish, validate, create and
independent verify operations. `qualify_dimensioned_part_drawing`,
`verify_qualified_dimensioned_part_drawing`, `qualify_final_part_drawing` and
`verify_qualified_final_part_drawing` are rejected unconditionally.

## Evidence gates

`release_candidate/contracts/h1-chain-evidence.schema.json` defines the closed evidence ledger.
`release_candidate/h1_chain_evidence.py` additionally checks:

- the exact five-Skill and globally contiguous operation order;
- one distinct response artifact for every semantic call and each Skill's production allow-list;
- a ready, clean H0 report bound to the same Git commit;
- unchanged source-model and drawing-template bytes;
- exact SHA-256 continuity from initializer outputs to ViewPlan inputs, then from the verified view
  drawing to DimensionPlan inputs, and finally from the verified dimension drawing to layout;
- exact canonical request and plan hashes across publish, validate, create and verify;
- the layout request's unchanged embedded DimensionPlanningRequest;
- exactly one new blank, view, dimensioned and final drawing path;
- final drawing/sidecar SHA binding and successful independent read-only verification; and
- immutable, publish-once final evidence output.

The H0 readiness artifact already inventories the five Skill files, three plan Schemas and four
capability manifests. H1 binds that report and adds the execution-service binary, three published
plans, all handoffs/drawings/sidecars and every semantic response, completing the release audit
chain without duplicating the H0 inventory.

## Validation command

After a real production run has captured the candidate ledger and response JSON files:

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_h1_five_skill_chain.py `
  --candidate C:\path\to\h1-chain-evidence.candidate.json `
  --output C:\path\to\h1-chain-evidence.json
```

The output path must be new. At present a real candidate must fail before this point because H0 is
blocked by the unpromoted F7 dimension registry. This is intentional: the H1 validator is complete
enough to accept a future production run, but it cannot be used to claim that such a run already
exists.
