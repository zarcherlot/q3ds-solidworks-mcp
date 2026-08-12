---
name: solidworks-initialize-drawing-handoff
description: Generate and verify the complete immutable initializer artifact handoff for repository-native SolidWorks ViewPlan drawing planning. Use when a saved .SLDPRT needs drawing-planning-handoff.json, a verified blank .SLDDRW, readiness and geometry reports, and six real standard-view PNGs before solidworks-create-drawing-views can plan or create a drawing.
---

# Initialize SolidWorks Drawing Handoff

Use the configured `solidpilot` MCP engineering-semantic tools. Do not call legacy tools,
private executor operations, raw HTTP, Python bridges, or SolidWorks COM.

## Required inputs

Require absolute paths for:

- one existing saved `.SLDPRT` source model;
- one existing `.DRWDOT` drawing template;
- one existing publication directory.

The following publication paths must all be absent:

- `drawing-planning-handoff.json`
- `initializer-blank.SLDDRW`
- `drawing-readiness.json`
- `model-geometry.json`
- `front.png`, `back.png`, `left.png`, `right.png`, `top.png`, `bottom.png`

Treat the source model, template, and every successfully published artifact as immutable.

## Workflow

1. Call `solidworks_status` with `{"launch_if_needed":true}`. Continue only when `ok=true`
   and `com_attached=true`.
2. Confirm all three input paths meet the requirements and every fixed output path is new.
3. Call `initialize_part_drawing_handoff` exactly once with:

   ```json
   {
     "model_path": "C:\\absolute\\part.SLDPRT",
     "drawing_template_path": "C:\\absolute\\template.DRWDOT",
     "publication_directory": "C:\\absolute\\job",
     "image_width": 1024,
     "image_height": 768
   }
   ```

4. Continue only when the response has `ok=true`, `status=COMPLETED`, `verified=true`, and
   `handoff_integrity=pass`.
5. Require `result.kind=drawing_planning_handoff`, an absolute manifest path named
   `drawing-planning-handoff.json`, a lowercase 64-character manifest SHA-256, and a complete
   `planning_request` using the `production` profile.
6. Report the manifest path/hash, blank drawing, reports, six images, configuration, display
   state, state version, and integrity result.

The C# transaction owns source-document restoration, real standard-view capture, topology
freezing, blank-drawing save/close/read-only-reopen verification, rollback, hashing, and
manifest-last commit. Never reproduce those steps in the Skill.

## Continue to drawing creation

If the user also requested drawing views, invoke `solidworks-create-drawing-views` after successful
initialization. Use the returned manifest path and reuse the returned `planning_request` unchanged.
Choose a new absolute `.SLDDRW` output path; never overwrite the initializer blank drawing, the
source drawing, or an existing verification sidecar.

## Failure rules

Stop without retrying through modified inputs when any of these occurs:

- SolidWorks cannot attach;
- the source has unsaved changes or changes hash during initialization;
- the template does not produce a blank drawing;
- any fixed output already exists or appears during commit;
- a standard view cannot be resolved or captured;
- save, close, read-only reopen, hash, rollback, or repository integrity validation fails.

Only the MCP layer may perform its single state-version resynchronization. Do not create a second
MCP client or invoke the private execution service directly.
