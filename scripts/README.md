# Bolt-gauge examples

For repository installation before the semantic MCP can start, use
`setup_repository_host.ps1`; its `Inspect`, `Configure`, and `Verify` modes are documented in
[`docs/REPOSITORY_HOST_SETUP.md`](../docs/REPOSITORY_HOST_SETUP.md). This is separate from the
SolidWorks-specific `bootstrap-solidworks-host` Skill.

These scripts record the SolidWorks 2026 development and verification flow used
to exercise SolidPilot's part-modeling tools. They are examples, not an
automated test suite, and they operate on a live SolidWorks session.

- `gauge_v4.py` is the latest complete gauge build.
- `gauge_stage*.py` and `gauge_v2_stage*.py` preserve smaller diagnostic stages.
- `gauge_text_test*.py`, `gauge_numbers_a.py`, and
  `gauge_extrude_text.py` document the sketch-text investigation. New clients
  should prefer the public `add_sketch_text` MCP tool over these low-level COM
  experiments.

Run examples with the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe .\scripts\gauge_v4.py
```

Generated parts, drawings, and captures default to the ignored `outputs/`
directory. Override that destination without editing a script:

```powershell
$env:SOLIDPILOT_OUTPUT_DIR = "C:\CAD\SolidPilot Outputs"
.\.venv\Scripts\python.exe .\scripts\gauge_v4.py
```

All geometry values passed to SolidPilot are in metres. Review a script before
running it: several diagnostic stages assume that a particular part or sketch
is already active.
