# H4 one-step production semantic broker

Status: the repository Codex stdio MCP can now execute and append-capture exactly one H3-authorized
production operation at a time. H4 does not generate plans, freeze stage artifacts or finalize H1.

## Boundary

H4 consumes one immutable `solidworks-five-skill-semantic-step-request` containing the H3 manifest
path/hash, exact sequence, exact tool and one strict JSON arguments object. Before starting MCP it:

1. revalidates the H3 manifest, H0/runtime/model/template hashes and frozen Git commit;
2. inspects every existing response and stage capture;
3. requires H3 status `awaiting_operation` and an exact sequence/tool match; and
4. permits only the 16 production operations frozen by H2.

Qualification tools, host repair, private executor verbs, HTTP endpoints and direct COM are not
legal H4 operations. A stage boundary returns `awaiting_stage_capture`; freeze that stage with the
H3 command before preparing the next H4 request.

## One call

Create a new request file without overwriting an earlier request:

```json
{
  "protocol_id": "solidworks-five-skill-semantic-step-request",
  "schema_version": "1.0",
  "session_manifest": {
    "path": "C:\\evidence\\session-r1\\session-manifest.json",
    "sha256": "<64 lowercase hex characters>"
  },
  "sequence": 1,
  "tool": "inspect_solidworks_host",
  "arguments": {
    "output_directory": "C:\\evidence\\session-r1\\host-inspection"
  }
}
```

Run exactly that step with its independently calculated file hash:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_h4_five_skill_step.py `
  --request C:\evidence\requests\01-inspect-host.json `
  --request-sha256 <sha256> `
  --diagnostics C:\evidence\session-r1\01-inspect-host.mcp.stderr.log
```

H4 starts only `adapters/codex/server.py`, checks the live 24-tool/zero-prompt surface, invokes the
one authorized tool, parses one JSON-object response and immediately passes it to H3's append-only
capture. It sets `EXECUTION_EXE_PATH` from the hash-bound H3 runtime rather than caller input.
Diagnostics, when requested, must be a new file inside the external session root.

## Failure semantics

A structured semantic failure is captured unchanged and permanently blocks the session. Once a
tool invocation starts, a timeout, transport loss, malformed JSON or unstable shutdown is
ambiguous: H4 captures a synthetic `h4-ambiguous-semantic-call` failure with `retry_safe: false`.
This deliberately forbids replay of an operation that may already have mutated a drawing.

Before process startup H4 atomically publishes a hash-bound call claim under
`responses/.h4-claims/`. The claim freezes the complete strict-JSON arguments plus their canonical
SHA-256, and also binds the H4 broker, Codex stdio entry, semantic contract and execution-service
binary hashes, so the final release audit can independently reproduce the exact call boundary. Concurrent
brokers therefore cannot invoke the same sequence twice. Only a
typed startup, discovery or argument-Schema failure proven to occur before `call_tool` releases the
claim; every post-invocation outcome retains it.

MCP startup or surface-discovery failure occurs before the tool call and therefore does not consume
the H3 sequence. The caller may repair that pre-call condition and submit the same immutable step
request again. The current unpromoted F7 capability keeps H0/H2 blocked, so this path cannot start a
real production SolidWorks chain until the production dimension registry is legitimately promoted.
