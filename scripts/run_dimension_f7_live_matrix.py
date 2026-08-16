"""Run the immutable DimensionPlan F7 qualification matrix through the semantic MCP.

Production create remains fail-closed while capabilities are planned.  Each F7 case instead uses
the public, matrix-bound qualification transaction, which accepts planned but never unsupported
capabilities and does not mutate the capability manifest.  Only the complete immutable summary can
produce a reviewable promotion candidate.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import sys
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.f7_evidence import (  # noqa: E402
    DimensionF7EvidenceError,
    build_f7_capability_promotion_candidate,
    build_f7_case_evidence_from_semantic_results,
    build_f7_summary,
    publish_json_once,
    validate_f7_matrix_request,
)


_EXPECTED_TOOLS = {
    "solidworks_status",
    "inspect_solidworks_host",
    "bootstrap_solidworks_host",
    "inspect_part_for_drawing",
    "initialize_part_drawing_handoff",
    "plan_part_drawing_views",
    "publish_validated_part_drawing_view_plan",
    "validate_part_drawing_view_plan",
    "create_part_drawing_from_view_plan",
    "verify_part_drawing_view_plan",
    "initialize_part_drawing_dimension_handoff",
    "publish_validated_part_drawing_dimension_plan",
    "validate_part_drawing_dimension_plan",
    "create_dimensioned_part_drawing",
    "verify_dimensioned_part_drawing",
    "qualify_dimensioned_part_drawing",
    "verify_qualified_dimensioned_part_drawing",
    "initialize_part_drawing_layout_handoff",
    "publish_validated_part_drawing_layout_plan",
    "validate_part_drawing_layout_plan",
    "qualify_final_part_drawing",
    "verify_qualified_final_part_drawing",
    "create_final_part_drawing",
    "verify_final_part_drawing",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the six-category DimensionPlan F7 live evidence matrix."
    )
    parser.add_argument("--request", required=True, help="Immutable F7 matrix request JSON")
    parser.add_argument("--summary-output", required=True, help="New immutable summary JSON")
    parser.add_argument(
        "--execution-service-path",
        required=True,
        help="Exact repository execution-service executable used by the matrix",
    )
    parser.add_argument(
        "--execution-pid",
        required=True,
        type=int,
        help="PID of the already-running repository execution service",
    )
    parser.add_argument(
        "--capability-manifest-path",
        default=str(REPOSITORY_ROOT / "dimension_planner" / "capabilities" / "current.json"),
        help="Exact capability manifest consumed by the semantic server",
    )
    parser.add_argument(
        "--promotion-candidate-output",
        help="Optional new JSON path for a manifest candidate; never replaces current.json",
    )
    parser.add_argument(
        "--diagnostics-path",
        help="Optional new stderr log path (defaults beside the summary)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print(f"F7 live matrix failed closed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


async def run(args: argparse.Namespace) -> dict[str, Any]:
    root = REPOSITORY_ROOT.resolve(strict=True)
    request_path = _existing_json(Path(args.request), "F7 request")
    summary_output = _new_json(Path(args.summary_output), "F7 summary")
    execution_service = _existing_file(
        Path(args.execution_service_path), "execution service"
    )
    capability_manifest = _existing_json(
        Path(args.capability_manifest_path), "capability manifest"
    )
    current_manifest = (
        root / "dimension_planner" / "capabilities" / "current.json"
    ).resolve(strict=True)
    if capability_manifest != current_manifest:
        raise ValueError(
            "the default semantic server consumes only the repository current capability manifest"
        )
    runtime_image = _process_image(args.execution_pid)
    if runtime_image != execution_service:
        raise RuntimeError(
            f"execution PID {args.execution_pid} is {runtime_image}, expected {execution_service}"
        )
    promotion_output = (
        _new_json(Path(args.promotion_candidate_output), "promotion candidate")
        if args.promotion_candidate_output
        else None
    )
    diagnostics = (
        _new_file(Path(args.diagnostics_path), "F7 diagnostics")
        if args.diagnostics_path
        else _new_file(
            summary_output.with_name(summary_output.name + ".mcp.stderr.log"),
            "F7 diagnostics",
        )
    )
    if args.timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be positive")

    raw_request = _load_json(request_path)
    request = validate_f7_matrix_request(raw_request)
    request_sha256 = _sha256(request_path)
    protected_before = _snapshot(
        _protected_files(
            request, request_path, capability_manifest, execution_service
        )
    )
    evidence_paths: list[Path] = []
    case_results: list[dict[str, Any]] = []

    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["EXECUTION_EXE_PATH"] = str(execution_service)
    params = StdioServerParameters(
        command=str(root / ".venv" / "Scripts" / "python.exe"),
        args=[str(root / "adapters" / "codex" / "server.py")],
        cwd=str(root),
        env=child_env,
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    with diagnostics.open("x", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                discovered = await session.list_tools()
                prompts = await session.list_prompts()
                names = {tool.name for tool in discovered.tools}
                if names != _EXPECTED_TOOLS:
                    raise RuntimeError(
                        "unexpected default MCP surface; "
                        f"missing={sorted(_EXPECTED_TOOLS - names)}, "
                        f"extra={sorted(names - _EXPECTED_TOOLS)}"
                    )
                if prompts.prompts:
                    raise RuntimeError("default semantic MCP must expose zero prompts")

                status = await _call(
                    session,
                    "solidworks_status",
                    {"launch_if_needed": False},
                    args.timeout_seconds,
                )
                if status.get("ok") is not True:
                    raise RuntimeError("SolidWorks execution service readiness check failed")
                # A managed semantic transaction owns its SolidWorks session. Do not keep a
                # pre-launched COM host alive across the matrix: every qualification and
                # verification call launches through the execution service and proves cleanup
                # before returning.

                for case in request["cases"]:
                    plan = _load_json(Path(case["plan_path"]))
                    arguments = {
                        "plan": plan,
                        "request": case["planning_request"],
                        "output_path": case["output_path"],
                    }
                    validate = await _call(
                        session,
                        "validate_part_drawing_dimension_plan",
                        arguments,
                        args.timeout_seconds,
                    )
                    _require_stage(validate, "validate", "VALID", case)
                    if validate.get("execution_readiness") != "capability_blocked":
                        raise RuntimeError(
                            f"{case['case_id']} must still be production capability_blocked "
                            "before qualification"
                        )
                    qualification_arguments = {
                        **arguments,
                        "matrix_request_path": str(request_path),
                        "matrix_request_sha256": request_sha256,
                        "case_id": case["case_id"],
                    }
                    create = await _call(
                        session,
                        "qualify_dimensioned_part_drawing",
                        qualification_arguments,
                        args.timeout_seconds,
                    )
                    _require_stage(create, "create", "COMPLETED", case)
                    verify = await _call(
                        session,
                        "verify_qualified_dimensioned_part_drawing",
                        qualification_arguments,
                        args.timeout_seconds,
                    )
                    _require_stage(verify, "verify", "COMPLETED", case)

                    evidence = build_f7_case_evidence_from_semantic_results(
                        case,
                        {"validate": validate, "create": create, "verify": verify},
                        execution_service_path=execution_service,
                        capability_manifest_path=capability_manifest,
                    )
                    evidence_path = Path(case["evidence_path"])
                    path, sha256 = publish_json_once(evidence, evidence_path)
                    evidence_paths.append(evidence_path)
                    case_results.append(
                        {
                            "case_id": case["case_id"],
                            "category": case["category"],
                            "evidence_path": path,
                            "evidence_sha256": sha256,
                        }
                    )

    protected_after = _snapshot(
        _protected_files(
            request, request_path, capability_manifest, execution_service
        )
    )
    if protected_before != protected_after:
        raise RuntimeError("F7 source, plan, request, capability, or executor inputs changed")

    summary = build_f7_summary(raw_request, evidence_paths)
    if summary["overall_status"] != "complete":
        raise DimensionF7EvidenceError("F7 matrix coverage is incomplete")
    summary_path, summary_sha256 = publish_json_once(summary, summary_output)

    promotion: dict[str, str] | None = None
    if promotion_output is not None:
        candidate = build_f7_capability_promotion_candidate(
            _load_json(capability_manifest), summary_output
        )
        candidate_path, candidate_sha256 = publish_json_once(candidate, promotion_output)
        promotion = {"path": candidate_path, "sha256": candidate_sha256}

    return {
        "ok": True,
        "status": "COMPLETED",
        "request_path": str(request_path),
        "summary": {"path": summary_path, "sha256": summary_sha256},
        "cases": case_results,
        "promotion_candidate": promotion,
        "diagnostics_path": str(diagnostics),
    }


async def _call(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    result = await session.call_tool(
        name,
        arguments,
        read_timeout_seconds=timedelta(seconds=timeout_seconds),
    )
    text_blocks = [
        block.text for block in result.content if getattr(block, "type", None) == "text"
    ]
    if result.isError:
        raise RuntimeError(f"MCP tool {name} failed: {' '.join(text_blocks)}")
    if len(text_blocks) != 1:
        raise RuntimeError(f"MCP tool {name} returned {len(text_blocks)} text blocks")
    try:
        payload = json.loads(text_blocks[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MCP tool {name} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP tool {name} returned no JSON object")
    return payload


def _require_stage(
    payload: dict[str, Any], label: str, expected_status: str, case: dict[str, Any]
) -> None:
    if payload.get("ok") is not True or payload.get("status") != expected_status:
        raise RuntimeError(
            f"{case['case_id']} {label} failed closed: "
            f"status={payload.get('status')}, error={payload.get('error')}"
        )
    if payload.get("planning_request_sha256") != case["planning_request_sha256"]:
        raise RuntimeError(f"{case['case_id']} {label} request hash mismatch")
    if payload.get("plan_canonical_sha256") != case["plan_canonical_sha256"]:
        raise RuntimeError(f"{case['case_id']} {label} plan hash mismatch")


def _protected_files(
    request: dict[str, Any],
    request_path: Path,
    capability_manifest: Path,
    execution_service: Path,
) -> list[Path]:
    paths = {
        request_path.resolve(),
        Path(request["f0_evidence"]["path"]).resolve(),
        capability_manifest.resolve(),
        execution_service.resolve(),
    }
    for case in request["cases"]:
        plan_path = Path(case["plan_path"]).resolve()
        paths.add(plan_path)
        plan = _load_json(plan_path)
        for key in (
            "handoff",
            "source_model",
            "source_drawing",
            "view_plan",
            "verification_sidecar",
        ):
            paths.add(Path(plan[key]["path"]).resolve())
    return sorted(paths, key=lambda path: os.path.normcase(str(path)))


def _snapshot(paths: list[Path]) -> dict[str, str]:
    return {str(path): _sha256(path) for path in paths}


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return value


def _existing_file(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be an existing file: {resolved}")
    return resolved


def _process_image(process_id: int) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("F7 execution runtime ownership verification requires Windows")
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
    if not process:
        raise RuntimeError(f"cannot open execution PID {process_id}")
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(size)
        ):
            raise RuntimeError(f"cannot resolve execution PID {process_id} image")
        return Path(buffer.value).resolve(strict=True)
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def _existing_json(path: Path, label: str) -> Path:
    resolved = _existing_file(path, label)
    if resolved.suffix.lower() != ".json":
        raise ValueError(f"{label} must be JSON: {resolved}")
    return resolved


def _new_file(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    resolved = path.resolve()
    if resolved.exists() or not resolved.parent.is_dir():
        raise ValueError(f"{label} must be new in an existing directory: {resolved}")
    return resolved


def _new_json(path: Path, label: str) -> Path:
    resolved = _new_file(path, label)
    if resolved.suffix.lower() != ".json":
        raise ValueError(f"{label} must use .json: {resolved}")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
