"""One-step production semantic MCP broker bound to an H3 session."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .h2_session_preflight import PRODUCTION_SCHEDULE
from .h3_session_capture import capture_h3_operation, inspect_h3_session


PACKAGE_ROOT = Path(__file__).resolve().parent
REQUEST_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h4-semantic-step-request.schema.json"
CLAIM_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h4-semantic-call-claim.schema.json"
SKILL_CHAIN_CONTRACT_PATH = (
    PACKAGE_ROOT.parent / "adapters" / "claude" / "contracts" / "skill-chain.contract.json"
)

SemanticCaller = Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]


class H4SemanticStepError(ValueError):
    """Raised when H4 cannot safely start or capture one semantic operation."""


class _PreCallMcpError(RuntimeError):
    """MCP failed before the requested semantic tool could be invoked."""


class _AmbiguousMcpCallError(RuntimeError):
    """The semantic call started but returned no trustworthy JSON response."""


def load_h4_step_request(path: Path, expected_sha256: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if _sha256(resolved) != expected_sha256:
        raise H4SemanticStepError("H4 step request SHA-256 mismatch")
    try:
        candidate = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise H4SemanticStepError("H4 step request must be UTF-8 JSON") from exc
    return validate_h4_step_request(candidate)


def validate_h4_step_request(candidate: Mapping[str, Any]) -> dict[str, Any]:
    request = _json_copy(candidate, "H4 step request")
    _validate(REQUEST_SCHEMA_PATH, request, "H4 step request")
    return request


async def run_h4_semantic_step(
    candidate: Mapping[str, Any],
    *,
    timeout_seconds: int = 900,
    semantic_caller: SemanticCaller | None = None,
    diagnostics_path: Path | None = None,
) -> dict[str, Any]:
    """Invoke and capture exactly the next H3 production semantic operation."""
    request = validate_h4_step_request(candidate)
    if timeout_seconds < 1:
        raise H4SemanticStepError("H4 timeout must be positive")

    manifest_binding = request["session_manifest"]
    manifest_path = Path(manifest_binding["path"])
    state = inspect_h3_session(manifest_path, manifest_binding["sha256"])
    manifest = _load_hash_bound_manifest(manifest_path, manifest_binding["sha256"])
    if state["status"] != "awaiting_operation":
        raise H4SemanticStepError(
            f"H4 requires an H3 session awaiting one operation, got {state['status']}"
        )
    if request["sequence"] != state["next_sequence"]:
        raise H4SemanticStepError(
            f"H4 expected sequence {state['next_sequence']}, got {request['sequence']}"
        )
    if request["tool"] != state["next_tool"]:
        raise H4SemanticStepError(
            f"H4 expected tool {state['next_tool']}, got {request['tool']}"
        )
    _validate_production_tool(request["tool"])
    _validate_diagnostics_path(manifest, diagnostics_path)

    claim = _acquire_call_claim(request, manifest)
    try:
        if semantic_caller is None:
            response = await _call_repository_semantic_mcp(
                manifest_path,
                manifest_binding["sha256"],
                manifest,
                request["tool"],
                request["arguments"],
                timeout_seconds,
                diagnostics_path,
            )
        else:
            response = await semantic_caller(request["tool"], request["arguments"])
        payload = _json_copy(response, "semantic MCP response")
    except _PreCallMcpError as exc:
        _release_pre_call_claim(Path(claim["path"]))
        raise H4SemanticStepError(str(exc)) from exc
    except Exception as exc:
        payload = {
            "ok": False,
            "status": "FAILED",
            "error": {
                "code": "h4-ambiguous-semantic-call",
                "message": str(exc) or type(exc).__name__,
                "retry_safe": False,
            },
        }

    capture = capture_h3_operation(
        manifest_path,
        manifest_binding["sha256"],
        request["tool"],
        payload,
    )
    return {
        "ok": capture["ok"],
        "status": "captured" if capture["ok"] else "blocked",
        "sequence": request["sequence"],
        "tool": request["tool"],
        "semantic_response": payload,
        "call_claim": claim,
        "capture": capture,
    }


async def _call_repository_semantic_mcp(
    manifest_path: Path,
    manifest_sha256: str,
    manifest: Mapping[str, Any],
    tool: str,
    arguments: dict[str, Any],
    timeout_seconds: int,
    diagnostics_path: Path | None,
) -> dict[str, Any]:
    # A second inspection immediately before process launch closes the gap between request
    # validation and MCP startup. It also yields the frozen repository/runtime bindings.
    inspect_h3_session(manifest_path, manifest_sha256)
    repository_root = Path(manifest["repository_root"]).resolve(strict=True)
    server_path = repository_root / "adapters" / "codex" / "server.py"
    if not server_path.is_file():
        raise _PreCallMcpError("repository Codex stdio MCP entry point is missing")
    python_path = _repository_python(repository_root)
    expected_tools, expected_prompt_count = _expected_surface(repository_root)

    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["EXECUTION_EXE_PATH"] = manifest["execution_service"]["path"]
    params = StdioServerParameters(
        command=str(python_path),
        args=[str(server_path)],
        cwd=str(repository_root),
        env=child_env,
        encoding="utf-8",
        encoding_error_handler="strict",
    )

    if diagnostics_path is None:
        diagnostics = tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="strict"
        )
        close_diagnostics = True
    else:
        resolved_diagnostics = diagnostics_path.resolve()
        diagnostics = resolved_diagnostics.open("x", encoding="utf-8")
        close_diagnostics = True
    tool_invoked = False
    try:
        async with stdio_client(params, errlog=diagnostics) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                discovered = await session.list_tools()
                prompts = await session.list_prompts()
                names = {item.name for item in discovered.tools}
                if names != expected_tools:
                    raise _PreCallMcpError(
                        "unexpected default MCP surface; "
                        f"missing={sorted(expected_tools - names)}, "
                        f"extra={sorted(names - expected_tools)}"
                    )
                if len(prompts.prompts) != expected_prompt_count:
                    raise _PreCallMcpError("default semantic MCP prompt surface drifted")
                selected = next(item for item in discovered.tools if item.name == tool)
                input_schema = getattr(selected, "inputSchema", None)
                if not isinstance(input_schema, dict):
                    raise _PreCallMcpError(f"MCP tool {tool} has no JSON input Schema")
                argument_errors = sorted(
                    Draft202012Validator(input_schema).iter_errors(arguments),
                    key=lambda error: list(error.absolute_path),
                )
                if argument_errors:
                    error = argument_errors[0]
                    location = "/".join(
                        str(part) for part in error.absolute_path
                    ) or "<root>"
                    raise _PreCallMcpError(
                        f"invalid arguments for MCP tool {tool} at {location}: "
                        f"{error.message}"
                    )
                try:
                    tool_invoked = True
                    result = await session.call_tool(
                        tool,
                        arguments,
                        read_timeout_seconds=timedelta(seconds=timeout_seconds),
                    )
                except Exception as exc:
                    raise _AmbiguousMcpCallError(
                        f"MCP tool {tool} ended without a response: {exc}"
                    ) from exc
                return _mcp_json_response(tool, result)
    except _PreCallMcpError:
        raise
    except _AmbiguousMcpCallError:
        raise
    except Exception as exc:
        if tool_invoked:
            raise _AmbiguousMcpCallError(
                f"MCP tool {tool} ended without a stable response: {exc}"
            ) from exc
        raise _PreCallMcpError(f"MCP startup/discovery failed before {tool}: {exc}") from exc
    finally:
        if close_diagnostics:
            diagnostics.close()


def _mcp_json_response(tool: str, result: Any) -> dict[str, Any]:
    blocks = [
        block.text
        for block in result.content
        if getattr(block, "type", None) == "text"
    ]
    if len(blocks) != 1:
        raise _AmbiguousMcpCallError(
            f"MCP tool {tool} returned {len(blocks)} text blocks"
        )
    try:
        payload = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise _AmbiguousMcpCallError(
            f"MCP tool {tool} returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise _AmbiguousMcpCallError(f"MCP tool {tool} returned no JSON object")
    return payload


def _expected_surface(repository_root: Path) -> tuple[set[str], int]:
    path = repository_root / SKILL_CHAIN_CONTRACT_PATH.relative_to(PACKAGE_ROOT.parent)
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))["default_mcp"]
        tools = contract["tools"]
        count = contract["tool_count"]
        prompt_count = contract["prompt_count"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _PreCallMcpError("five-Skill MCP surface contract is invalid") from exc
    if (
        not isinstance(tools, list)
        or len(tools) != count
        or len(set(tools)) != count
        or count != 24
        or prompt_count != 0
    ):
        raise _PreCallMcpError("five-Skill MCP surface contract drifted")
    return set(tools), prompt_count


def _validate_production_tool(tool: str) -> None:
    production_tools = {item[2] for item in PRODUCTION_SCHEDULE}
    if tool not in production_tools:
        raise H4SemanticStepError("H4 permits only the frozen production schedule")
    if "qualify" in tool or "executor" in tool or tool.startswith("bootstrap_"):
        raise H4SemanticStepError("H4 forbids qualification, private executor and repair tools")


def _acquire_call_claim(
    request: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, str]:
    repository_root = PACKAGE_ROOT.parent
    try:
        response_directory = Path(manifest["planned_outputs"]["response_directory"])
    except (KeyError, TypeError) as exc:
        raise H4SemanticStepError("H4 could not resolve the response claim directory") from exc
    claim_directory = response_directory / ".h4-claims"
    claim_directory.mkdir(exist_ok=True)
    claim_path = claim_directory / (
        f"{request['sequence']:02d}-{request['tool']}.json"
    )
    claim_value = {
        "protocol_id": "solidworks-five-skill-semantic-call-claim",
        "schema_version": "1.0",
        "session_manifest_sha256": request["session_manifest"]["sha256"],
        "sequence": request["sequence"],
        "tool": request["tool"],
        "arguments": request["arguments"],
        "arguments_sha256": hashlib.sha256(
            json.dumps(
                request["arguments"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "broker_sha256": _sha256(Path(__file__).resolve(strict=True)),
        "server_entry_sha256": _sha256(
            repository_root / "adapters" / "codex" / "server.py"
        ),
        "semantic_contract_sha256": _sha256(
            repository_root
            / "adapters" / "claude" / "contracts" / "skill-chain.contract.json"
        ),
        "execution_service_sha256": manifest["execution_service"]["sha256"],
    }
    _validate(CLAIM_SCHEMA_PATH, claim_value, "H4 semantic call claim")
    try:
        with claim_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(claim_value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise H4SemanticStepError(
            "the current H4 operation already has a call claim; replay is forbidden"
        ) from exc
    return {"path": str(claim_path.resolve(strict=True)), "sha256": _sha256(claim_path)}


def _release_pre_call_claim(path: Path) -> None:
    # Only a typed pre-call failure reaches this function; the semantic tool was never invoked.
    try:
        path.unlink()
    except OSError as exc:
        raise H4SemanticStepError(
            "H4 pre-call failed and its provisional claim could not be released"
        ) from exc


def _validate_diagnostics_path(
    manifest: Mapping[str, Any], diagnostics_path: Path | None
) -> None:
    if diagnostics_path is None:
        return
    if not diagnostics_path.is_absolute():
        raise H4SemanticStepError("H4 diagnostics path must be absolute")
    session_root = Path(manifest["session_root"]).resolve(strict=True)
    resolved = diagnostics_path.resolve()
    if session_root not in resolved.parents:
        raise H4SemanticStepError("H4 diagnostics must remain inside the session root")
    if resolved.exists() or not resolved.parent.is_dir():
        raise H4SemanticStepError(
            "H4 diagnostics must be a new file in an existing session directory"
        )


def _load_hash_bound_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    try:
        raw = path.resolve(strict=True).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise H4SemanticStepError("H4 session manifest SHA-256 mismatch")
        value = json.loads(raw.decode("utf-8"))
    except H4SemanticStepError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise H4SemanticStepError("H4 session manifest is not stable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise H4SemanticStepError("H4 session manifest must be a JSON object")
    return value


def _repository_python(repository_root: Path) -> Path:
    candidates = (
        repository_root / ".venv" / "Scripts" / "python.exe",
        repository_root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(strict=True)
    executable = Path(sys.executable)
    if executable.is_file():
        return executable.resolve(strict=True)
    raise _PreCallMcpError("no Python runtime is available for the Codex stdio MCP")


def _validate(schema_path: Path, value: Any, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise H4SemanticStepError(f"invalid {label} at {location}: {error.message}")


def _json_copy(value: Any, label: str) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise H4SemanticStepError(f"{label} must contain only strict JSON values") from exc
    if not isinstance(copied, dict):
        raise H4SemanticStepError(f"{label} must be a JSON object")
    return copied


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
