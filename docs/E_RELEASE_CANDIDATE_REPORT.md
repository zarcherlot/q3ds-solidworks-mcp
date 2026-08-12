# E three-Skill release-candidate report

Date: 2026-08-12

Branch: `feature/e-skill-chain-closure`

Result: E1-E3 pass; E4 evidence complete except final Git commit hash

Host: SolidWorks 2025 SP5, revision `33.5.0`

## Scope and conclusion

The repository-owned production entry is frozen as:

`bootstrap-solidworks-host` -> `solidworks-initialize-drawing-handoff` ->
`solidworks-create-drawing-views`.

The default Codex route uses the explicit Skill publication branch. MCP Sampling is mutually
exclusive and requires an explicit user request. The real E3 run discovered exactly 10 semantic
stdio MCP tools and zero prompts, initialized once, generated one candidate, published once,
validated through both Python and C#, created one new associated drawing transactionally, and then
performed an independent read-only verification.

The E3 result is a pass. E4 is not declared complete because the working tree has intentionally not
been committed without user authorization. The evidence below is final for the current tree; after
the user requests a commit, record that commit SHA-1 and rerun the short hash/diff checks.

## E1-E3 acceptance

| Gate | Result | Evidence |
|---|---:|---|
| Three-Skill order and allow-lists | pass | `skill-chain.contract.json` plus contract tests |
| Default MCP surface | 10 tools, 0 prompts | real stdio discovery and schema/config drift tests |
| Planning branch rules | pass | explicit publish default; Sampling opt-in and mutually exclusive |
| Candidate/publication count | 1 / 1 | live report `counts` |
| Planning request continuity | pass | `a840660f0e5f8608c4e5452fe5f9951a8614a5ec42ab5c7703c1592e8b56da55` |
| Canonical plan continuity | pass | `6321114e35563c6d58bc44e0c7ea63ac0c1ec0ec28234678a428ee90f0ad449e` |
| Create and independent verify | pass | both `COMPLETED`, drawing and audit sidecar committed |
| Protected inputs | unchanged | source model, template, `validation/`, handoff and published plan |
| Private-operation escape | absent | Skill boundary tests reject executor verbs, raw HTTP, second MCP, COM, UI and legacy bridge |

Accepted live evidence:

- `.host-preflight/e3-host-20260812-r3/host-preflight-report.json`
- `.host-preflight/e3-positive-r3d-20260812/e3-skill-chain-live-report.json`
- `.host-preflight/e3-positive-r3d-20260812/view_plan.json`
- `.host-preflight/e3-positive-r3d-20260812/E3-固定法兰视图.SLDDRW`
- `.host-preflight/e3-positive-r3d-20260812/E3-固定法兰视图.SLDDRW.verification.json`

The live runner used an isolated repository runtime on `http://localhost:5013`; the default remains
`http://localhost:5000`. The override accepts only an explicit loopback HTTP origin and prevents an
unrelated local MCP session from invalidating runtime-ownership evidence.

## Negative matrix

| Case | Expected result | Evidence |
|---|---|---|
| Host blocked | stop before initialization | runner fail-closed unit test and semantic host tests |
| Handoff hash drift | integrity rejection | `test_changed_artifact_is_rejected` |
| Plan rejection | no publication | `.host-preflight/e3-user-chain-20260812/negative-plan-rejection.result.json` |
| Capability blocked | publication allowed, execution rejected | semantic tool and planner capability tests |
| Output path collision | reject before create | runner collision test and validation-matrix test |
| Verify/business mismatch | fail closed | runner business-status test and C# verification contracts |
| Request/plan hash mismatch | fail closed | runner request and canonical-plan binding tests |
| Runtime ownership mismatch | stop before MCP business calls | live r3c ownership failures and executable-image gate |

The rejected centerline attempt in `.host-preflight/e3-positive-r3b-20260812` is also retained. The
transaction rolled back with `VIEW_PLAN_CENTER_ELEMENT_CREATION_FAILED`; no drawing was committed.
The accepted r3d candidate removed the unsupported vertical line because the half-flange view has
no opposed visible linear-edge pair. Eight feature-bound center marks remain verified.

## Frozen SHA-256 inventory

| Artifact | SHA-256 |
|---|---|
| `bootstrap-solidworks-host/SKILL.md` | `0486ae0d119fd3a2ea926bea9c89b276a7c04733cd1a100f933c4cbcbc7acbd6` |
| `solidworks-initialize-drawing-handoff/SKILL.md` | `6a98ab6f82fe473ebef66bf0fe4dfe05fd977cca6f2d612f0167b56f0f9cd2b7` |
| `solidworks-create-drawing-views/SKILL.md` | `12d0aaad2b59451639524cf97be4fc4b2ab6ee793339d63005e56072cd1a41b6` |
| Skill-chain contract | `146703fc2326690faffb67f1f1536912d5faea5d167bbb792c1e5c00fb4d6403` |
| Semantic MCP schema | `39ce95151a045e8b660cb1554b62c53591e5328e48633cea95ddac02b27c20ff` |
| ViewPlan 1.4 schema | `ebe92b04bd1b4a4f0fd7ff6a6314e36f531e06421b0ae8f803fbb86ab209ceac` |
| `native-v4/manifest.json` | `f162f60c67a352b2f3f402d67664b0623614a68b4dac5e4030f4bc0b5250d280` |
| `native-v4/system.md` | `078604a6601ff2c28fe63fdd42b0ead6ad04296202749dd2bbe92a1851225171` |
| `native-v4/task.md` | `9e03c20d169ac79774ac2e8a8ebd8fc8aba0bdbb0f28a58d4a317effd4152633` |
| Capability manifest | `e9c31351aaddec5b25bf7cf7ff1e55df56f4180f1e67aa466dff6386c01219b5` |
| C# runtime | `24d41f1a9d3d7dd1a0037886f610be0007bc142b49ffa3a96862c646045989ac` |
| HostBootstrap helper | `d9bb6c83b89125e94c202e308c69a9a25c7edb6bd863106eadf4071f6c67671a` |
| Host preflight report | `a63b8ea640272ee42031799ca6d2777299863fc3555f75b5fb794357ff0d7829` |
| Source model | `f5b4186f2278f6e843d3bfacade15497b421f4c29160d304fd65663e53da6c99` |
| Drawing template | `e1dac5f026bc6c2167fe150af3f8d843e4136bcdca38015354153d78913a4a8e` |
| Handoff manifest | `2b3e0e71f8803ac3c531ea6cb104074d0341f3f1cd0711628acf7b4a701a75da` |
| Initializer blank drawing | `4647c75e4d86ceb7d9e8dd0709af44a89195ecfe5d1fb0b33ca49a9a10e7978b` |
| Drawing readiness report | `23055ecbe2c4c9a3a94980ca5761e3ca42f572765cf4cfb1e96ab9a34528c6e2` |
| Model geometry report | `fdd80194815651338417a9d95b301a068beda842da8953667ad2ab2005cd98bc` |
| Front image | `fd3c286f58fa9ea4dbcf33b73681ba362154991458dd9a4a18484bfb3a88141b` |
| Back image | `f3f654c00c00bf5aa4463c7086044c16f173571ad55aa657321b8d754e8658ec` |
| Left image | `e1ee6fe1a9bd8bb75f5c80195e83dd8782c6abd6637a763176f2844b5900a8bd` |
| Right image | `f205858778561139d802936f1b0f3634a858e932c867db8cbe424f0314ac89d7` |
| Top image | `ee15106662779d320f445c846f19f764bc3bd88f2ff8c5089bd51ce1a7b318f5` |
| Bottom image | `7fea6db6918d31979de2e4dcc8608e7d392d5c9e2320cce091ef63027ac67f69` |
| Published ViewPlan bytes | `33ba7045d2485149621c04b845e679c2f2e17baf71552422074ea8b02ba6bc37` |
| Created drawing | `806a49ee7d6f497bf85c4a19c1b6f6545755c596590034a3092fb6da94aec9a6` |
| Verification sidecar | `3e49cffa84358cee1e5b8cf2fcc69ac54d2cacb6adc7e911294adeabada9cce3` |

Git base before the E changes: `2cd6bf979a3c01ddcb62d946fedbdd6cc3c42606`.

Final E commit: pending user-authorized commit.

## Reproduction

Use a fresh publication directory and a free loopback port:

```powershell
.\.venv\Scripts\python.exe scripts\run_skill_chain_live.py `
  --repository-root . `
  --model-path <absolute-source.SLDPRT> `
  --drawing-template-path <absolute-template.DRWDOT> `
  --candidate-template <validated-candidate-template.json> `
  --publication-directory <fresh-publication-directory> `
  --output-path <fresh-publication-directory>\drawing.SLDDRW `
  --host-preflight-report <host-preflight-report.json> `
  --execution-exe <runtime>\SolidworksExecution.exe `
  --execution-base-url http://localhost:5013 `
  --start-execution-runtime `
  --validation-directory validation `
  --plan-id <new-stable-plan-id>
```

The candidate template is design input only. The runner rebinds every immutable handoff path/hash,
sheet property and geometry-evidence path to the newly initialized handoff before the one permitted
publication.
