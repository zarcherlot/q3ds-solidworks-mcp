# H5-H9 five-Skill release closure

Status: all COM-free closure contracts, auditors, freeze inventory and immutable publication are
implemented. A `complete` candidate still requires one real H0-ready, H3/H4-executed production
chain; synthetic or qualification evidence cannot satisfy the gate.

## Closure gates

- H5 revalidates H1 and H3, exact five-Skill/16-operation order, cross-stage path/hash continuity,
  exactly one ViewPlan, DimensionPlan and DrawingLayoutPlan, and four distinct successor drawings.
- H6 validates all three plan documents against their repository Draft 2020-12 Schemas, rechecks
  the four H0 capability bindings, and freezes the validator/compiler/preflight/transaction/verifier
  C# source families used by the hash-bound execution runtime.
- H7 independently discovers the 24-tool/zero-prompt FastMCP surface, compares contract, Codex
  configuration, tool Schema and five Skill allow-lists, then validates all 16 H4 exclusive call
  claims. Each claim contains the complete strict-JSON arguments and a reproducible canonical hash.
- H8 validates committed ViewPlan evidence plus the strict dimension/layout verification sidecars,
  exact frozen-input ledgers, in-memory and save/reopen results, stable final layout fingerprint and
  three independent read-only reopen responses.
- H9 requires the exact clean H0/H1 commit and builds one deduplicated final inventory. It re-hashes
  the five Skills, semantic contracts, three Schemas/plans, four capabilities, execution runtime,
  C# sources, source inputs, handoffs, four drawings, three sidecars, 16 responses and 16 call
  claims before publication.

## Request

Create a new request that binds the completed H3 manifest and the H1 candidate produced by H3:

```json
{
  "protocol_id": "solidworks-five-skill-release-closure-request",
  "schema_version": "1.0",
  "h3_session_manifest": {
    "path": "C:\\evidence\\session-r1\\session-manifest.json",
    "sha256": "<64 lowercase hex characters>"
  },
  "h1_chain_evidence": {
    "path": "C:\\evidence\\session-r1\\h1-chain-evidence.candidate.json",
    "sha256": "<64 lowercase hex characters>"
  }
}
```

The H1 path must be the exact `h1_candidate` path frozen by H2/H3. Every H4 operation must already
have left its claim under `responses/.h4-claims/`, all five H3 stage captures must exist, and the
repository must still be at the clean commit frozen by H0.

## Final publication

Run the COM-free closure audit with the independently calculated request-file hash:

```powershell
.\.venv\Scripts\python.exe .\scripts\finalize_h5_h9_release_candidate.py `
  --request C:\evidence\session-r1\h5-h9-release-request.json `
  --request-sha256 <sha256> `
  --output C:\evidence\session-r1\five-skill-release-candidate.json `
  --repository-root D:\solidworks-mcp
```

The output must be a new JSON file outside the repository and `validation/`. Publication uses a
no-overwrite atomic link and returns the final report SHA-256. Failure exits with code `2` and does
not publish a partial candidate.

The present repository cannot produce this final artifact because the F7 production dimension
registry remains unpromoted, so H0/H2 correctly prevent a real session. That is a live-evidence
prerequisite, not an unimplemented H-stage code path.
