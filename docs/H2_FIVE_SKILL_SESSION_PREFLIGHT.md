# H2 five-Skill production-session preflight

Status: COM-free production-session request, deterministic schedule and publish-once preflight are
implemented. Creating the live session and invoking SolidWorks remain gated on a `ready` report.

## Boundary

H2 freezes the exact inputs and output namespace before any five-Skill production call. It consumes
one H0 readiness report, Git commit, repository execution-service binary, source `.SLDPRT`, drawing
`.DRWDOT` and a new absolute session root. It never creates the session directory and never starts
or contacts SolidWorks.

The preflight always emits the same minimal 16-operation production schedule:

- host inspection;
- initializer handoff creation;
- ViewPlan publish, validate, create and independent verify;
- dimension handoff creation plus DimensionPlan publish, validate, production create and
  independent verify; and
- layout handoff creation plus DrawingLayoutPlan publish, validate, production create and
  independent verify.

The schedule contains neither F7 nor G7 qualification tools. Plan publication and each initializer
or drawing creation are marked mutating; validators and independent verifiers are read-only.

## Frozen output namespace

The session root deterministically assigns paths for the initializer handoff, blank drawing,
ViewPlan, view drawing and sidecar, dimension handoff/plan/drawing/sidecar, layout
handoff/plan/final drawing/sidecar, semantic response captures, stage manifests and the H1 evidence
candidate. The root must be new, its parent must already exist, and it cannot be under
`validation/`.

The request and report contracts are:

- `release_candidate/contracts/h2-session-request.schema.json`
- `release_candidate/contracts/h2-session-preflight.schema.json`

Run the COM-free preflight with:

```powershell
.\.venv\Scripts\python.exe .\scripts\prepare_h2_five_skill_session.py `
  --request C:\path\to\h2-session-request.json `
  --output C:\path\to\h2-session-preflight.json `
  --repository-root D:\solidworks-mcp
```

Exit code `0` means the future session may be created, `2` means a valid blocked report was
published, and `1` means the request/report contract itself failed. A blocked result preserves the
planned paths and blocker inventory for diagnosis but creates no live output directory.

## Current state

The current repository must produce `blocked`: F7 production dimensions have not been promoted and
the H0 report therefore cannot be `ready`. Local uncommitted changes are an independent blocker.
This does not prevent H2 development or testing; it prevents the preflight from authorizing a live
production run before its prerequisites exist.
