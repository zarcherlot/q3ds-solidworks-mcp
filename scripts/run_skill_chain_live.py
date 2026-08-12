"""Reproducible E3 three-Skill user-entry smoke over the default stdio MCP."""

from __future__ import annotations

import argparse
import asyncio
import copy
import ctypes
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


_EXPECTED_TOOLS = (
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
)
_VIEWS = ("front", "back", "left", "right", "top", "bottom")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--drawing-template-path", required=True)
    parser.add_argument("--candidate-template", required=True)
    parser.add_argument("--publication-directory", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--host-preflight-report", required=True)
    parser.add_argument("--execution-pid", type=int)
    parser.add_argument("--execution-exe", required=True)
    parser.add_argument("--execution-base-url", required=True)
    parser.add_argument("--start-execution-runtime", action="store_true")
    parser.add_argument("--validation-directory")
    parser.add_argument("--plan-id", required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print(f"E3 Skill-chain live run failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


async def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repository_root).resolve(strict=True)
    model = Path(args.model_path).resolve(strict=True)
    template = Path(args.drawing_template_path).resolve(strict=True)
    candidate_template = Path(args.candidate_template).resolve(strict=True)
    publication = Path(args.publication_directory).resolve()
    output = Path(args.output_path).resolve()
    preflight_path = Path(args.host_preflight_report).resolve(strict=True)
    execution_exe = Path(args.execution_exe).resolve(strict=True)
    validation = (
        Path(args.validation_directory).resolve(strict=True)
        if args.validation_directory
        else None
    )
    _require_new_publication_directory(publication)
    _require_new_output(output, publication)
    preflight = _load_json(preflight_path)
    _require_host_pass(preflight, template)
    runtime_process: subprocess.Popen[str] | None = None
    if args.start_execution_runtime:
        if args.execution_pid is not None:
            raise ValueError("execution PID and runtime auto-start are mutually exclusive")
        _require_port_free(args.execution_base_url)
        runtime_stdout = publication.parent / f"{publication.name}.runtime.stdout.log"
        runtime_stderr = publication.parent / f"{publication.name}.runtime.stderr.log"
        runtime_process = subprocess.Popen(
            [str(execution_exe), "--base-url", args.execution_base_url],
            cwd=execution_exe.parent,
            stdout=runtime_stdout.open("w", encoding="utf-8"),
            stderr=runtime_stderr.open("w", encoding="utf-8"),
            text=True,
            creationflags=0x08000000,
        )
        _wait_for_port(args.execution_base_url, runtime_process)
        execution_pid = runtime_process.pid
    elif args.execution_pid is not None:
        execution_pid = args.execution_pid
    else:
        raise ValueError("provide --execution-pid or --start-execution-runtime")
    runtime_image = _process_image(execution_pid)
    if runtime_image != execution_exe:
        raise RuntimeError(
            f"runtime ownership mismatch: PID {execution_pid} is {runtime_image}, "
            f"expected {execution_exe}"
        )

    protected = [model, template]
    if validation is not None:
        protected.extend(_files_under(validation))
    protected_before = _snapshot(protected)
    publication.mkdir(parents=True, exist_ok=True)
    diagnostics_path = publication.parent / f"{publication.name}.mcp.stderr.log"
    child_env = os.environ.copy()
    child_env["EXECUTION_BASE_URL"] = args.execution_base_url
    params = StdioServerParameters(
        command=str(root / ".venv" / "Scripts" / "python.exe"),
        args=[str(root / "adapters" / "codex" / "server.py")],
        cwd=str(root),
        env=child_env,
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    started = time.time()
    responses: dict[str, Any] = {}
    try:
        with diagnostics_path.open("w", encoding="utf-8") as diagnostics:
            async with stdio_client(params, errlog=diagnostics) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    discovered = await session.list_tools()
                    prompts = await session.list_prompts()
                    tool_names = tuple(tool.name for tool in discovered.tools)
                    _require_surface(tool_names, prompts.prompts)

                    responses["status"] = await _call(
                        session, "solidworks_status", {"launch_if_needed": False}
                    )
                    _require_host_status(responses["status"])

                    responses["initialize"] = await _call(
                        session,
                        "initialize_part_drawing_handoff",
                        {
                            "model_path": str(model),
                            "drawing_template_path": str(template),
                            "publication_directory": str(publication),
                            "image_width": 1024,
                            "image_height": 768,
                        },
                    )
                    _require_business(responses["initialize"], "initializer", "COMPLETED")
                    request = responses["initialize"].get("planning_request")
                    request_hash = responses["initialize"].get("planning_request_sha256")
                    if not isinstance(request, dict) or not _is_sha256(request_hash):
                        raise RuntimeError("initializer returned no complete planning request binding")

                    manifest = _load_json(Path(request["handoff_manifest_path"]))
                    candidate = _bind_candidate(
                        _load_json(candidate_template), manifest, root, args.plan_id
                    )
                    frozen_before = _snapshot(_handoff_files(manifest))

                    responses["publish"] = await _call(
                        session,
                        "publish_validated_part_drawing_view_plan",
                        {"plan": candidate, "request": request},
                    )
                    _require_business(responses["publish"], "publication", "published")
                    if responses["publish"].get("execution_readiness") != "supported":
                        raise RuntimeError("published plan is capability blocked")
                    _require_request_binding(
                        request_hash, responses["publish"].get("audit", {}).get("request_sha256"), "publish"
                    )
                    published_path = Path(responses["publish"]["plan"]["path"])
                    published = _load_json(published_path)
                    published_hash = _sha(published_path)
                    if published_hash != responses["publish"]["plan"]["sha256"]:
                        raise RuntimeError("published ViewPlan byte hash mismatch")

                    responses["validate"] = await _call(
                        session,
                        "validate_part_drawing_view_plan",
                        {"plan": published, "request": request},
                    )
                    _require_business(responses["validate"], "validation", "VALID")
                    _require_business(
                        responses["validate"].get("executor", {}),
                        "C# validation",
                        "COMPLETED",
                    )
                    _require_request_binding(
                    request_hash,
                    responses["validate"].get("planning_request_sha256"),
                    "validate",
                )
                    canonical_hash = responses["validate"].get("plan_canonical_sha256")
                    if not _is_sha256(canonical_hash):
                        raise RuntimeError("validation returned no canonical plan hash")

                    responses["create"] = await _call(
                        session,
                        "create_part_drawing_from_view_plan",
                        {"plan": published, "request": request, "output_path": str(output)},
                    )
                    _require_business(responses["create"], "create", "COMPLETED")
                    _require_bound_operation(responses["create"], request_hash, canonical_hash, "create")
                    sidecar = Path(str(output) + ".verification.json")
                    if not output.is_file() or not sidecar.is_file():
                        raise RuntimeError("create did not commit both drawing and audit sidecar")

                    responses["verify"] = await _call(
                        session,
                        "verify_part_drawing_view_plan",
                        {"plan": published, "request": request, "output_path": str(output)},
                    )
                    _require_business(responses["verify"], "independent verify", "COMPLETED")
                    _require_bound_operation(responses["verify"], request_hash, canonical_hash, "verify")
    finally:
        if runtime_process is not None and runtime_process.poll() is None:
            runtime_process.terminate()
            try:
                runtime_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                runtime_process.kill()
                runtime_process.wait(timeout=10)

    frozen_after = _snapshot(_handoff_files(manifest))
    protected_after = _snapshot(protected)
    if frozen_before != frozen_after:
        raise RuntimeError("published upstream handoff artifacts changed during create/verify")
    if protected_before != protected_after:
        raise RuntimeError("source model, template, or validation inputs changed")
    if _load_json(published_path) != published or _sha(published_path) != published_hash:
        raise RuntimeError("published ViewPlan changed after publication")

    report = {
        "schema_version": "1.0",
        "status": "pass",
        "chain": [
            "bootstrap-solidworks-host",
            "solidworks-initialize-drawing-handoff",
            "solidworks-create-drawing-views",
        ],
        "mcp": {
            "transport": "stdio",
            "server": initialized.serverInfo.name,
            "tools": list(tool_names),
            "tool_count": len(tool_names),
            "prompt_count": len(prompts.prompts),
        },
        "counts": {"initializer": 1, "candidate": 1, "publication": 1, "verify": 1},
        "runtime": {
            "pid": execution_pid,
            "path": str(execution_exe),
            "sha256": _sha(execution_exe),
            "base_url": args.execution_base_url,
        },
        "host_preflight": _artifact(preflight_path),
        "planning_request_sha256": request_hash,
        "plan_canonical_sha256": canonical_hash,
        "artifacts": {
            "source_model": _artifact(model),
            "drawing_template": _artifact(template),
            "candidate_template": _artifact(candidate_template),
            "handoff": _artifact(Path(request["handoff_manifest_path"])),
            "blank_drawing": _artifact(Path(manifest["blank_drawing"]["path"])),
            "readiness_report": _artifact(Path(manifest["readiness_report"]["path"])),
            "geometry_report": _artifact(Path(manifest["geometry_report"]["path"])),
            "standard_view_images": {
                row["view"]: _artifact(Path(row["path"]))
                for row in manifest["standard_view_images"]
            },
            "view_plan": _artifact(published_path),
            "drawing": _artifact(output),
            "verification_sidecar": _artifact(sidecar),
        },
        "immutability": {
            "source_model_template_validation_unchanged": True,
            "handoff_artifacts_unchanged_after_publication": True,
            "published_plan_unchanged": True,
        },
        "responses": responses,
        "duration_seconds": round(time.time() - started, 3),
    }
    report_path = publication / "e3-skill-chain-live-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


async def _call(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    result = await session.call_tool(
        name, arguments, read_timeout_seconds=timedelta(seconds=300)
    )
    if result.isError:
        details = " ".join(
            block.text for block in result.content if getattr(block, "type", None) == "text"
        )
        raise RuntimeError(f"MCP tool {name} failed: {details}")
    blocks = [
        block.text for block in result.content if getattr(block, "type", None) == "text"
    ]
    if len(blocks) != 1:
        raise RuntimeError(f"MCP tool {name} returned {len(blocks)} text blocks")
    payload = json.loads(blocks[0])
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP tool {name} returned no JSON object")
    return payload


def _bind_candidate(
    candidate: dict[str, Any], manifest: dict[str, Any], root: Path, plan_id: str
) -> dict[str, Any]:
    plan = copy.deepcopy(candidate)
    plan["$schema"] = str(root / "drawing_planner/contracts/view-plan.schema.json")
    plan["plan_id"] = plan_id
    plan["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    plan["model_path"] = manifest["model"]["path"]
    plan["model_sha256"] = manifest["model"]["sha256"]
    plan["configuration"] = manifest["model"]["configuration"]
    plan["display_state"] = manifest["model"].get("display_state")
    plan["drawing_path"] = manifest["blank_drawing"]["path"]
    plan["drawing_sha256"] = manifest["blank_drawing"]["sha256"]
    plan["geometry_report_path"] = manifest["geometry_report"]["path"]
    plan["geometry_report_sha256"] = manifest["geometry_report"]["sha256"]
    plan["readiness_report_path"] = manifest["readiness_report"]["path"]
    plan["readiness_report_sha256"] = manifest["readiness_report"]["sha256"]
    plan["standard_view_images"] = copy.deepcopy(manifest["standard_view_images"])
    context = manifest["drawing_context"]
    plan["sheet"] = copy.deepcopy(context["sheet"])
    plan["projection_method"] = context["projection_method"]
    plan["sheet_scale"] = copy.deepcopy(context["sheet_scale"])
    plan["inner_frame"] = copy.deepcopy(context["inner_frame"])
    plan["reserved_zones"] = copy.deepcopy(context["reserved_zones"])
    _rewrite_report_paths(plan, manifest["geometry_report"]["path"])
    for view in plan.get("views", []):
        view["symmetry_centerlines"] = [
            row
            for row in view.get("symmetry_centerlines", [])
            if row.get("id") != "cl-front-vertical"
        ]
    return plan


def _rewrite_report_paths(value: Any, geometry_path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "report_path":
                value[key] = geometry_path
            else:
                _rewrite_report_paths(item, geometry_path)
    elif isinstance(value, list):
        for item in value:
            _rewrite_report_paths(item, geometry_path)


def _require_surface(tools: Iterable[str], prompts: Iterable[Any]) -> None:
    names = tuple(tools)
    if len(names) != len(_EXPECTED_TOOLS) or set(names) != set(_EXPECTED_TOOLS):
        raise RuntimeError(f"unexpected default MCP surface: {names}")
    if tuple(prompts):
        raise RuntimeError("default MCP surface must expose zero prompts")


def _require_business(payload: dict[str, Any], label: str, status: str) -> None:
    if payload.get("ok") is not True or payload.get("status") != status:
        error = payload.get("error") or {}
        raise RuntimeError(
            f"{label} failed closed: {payload.get('status')} "
            f"{error.get('code')} {error.get('message')}"
        )


def _require_host_status(payload: dict[str, Any]) -> None:
    if payload.get("ok") is not True or payload.get("com_attached") is not True:
        raise RuntimeError("host status is not ready with COM attached")
    if not isinstance(payload.get("state_version"), int):
        raise RuntimeError("host status returned no authoritative state version")


def _require_request_binding(expected: str, actual: Any, label: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{label} planning request hash mismatch")


def _require_bound_operation(
    payload: dict[str, Any], request_hash: str, plan_hash: str, label: str
) -> None:
    _require_request_binding(request_hash, payload.get("planning_request_sha256"), label)
    if payload.get("plan_canonical_sha256") != plan_hash:
        raise RuntimeError(f"{label} canonical plan hash mismatch")


def _require_host_pass(report: dict[str, Any], template: Path) -> None:
    if (
        report.get("status") != "pass"
        or report.get("blocking_issues")
        or report.get("warnings")
    ):
        raise RuntimeError("host preflight is not an unqualified pass")
    template_report = report.get("template") or {}
    if not template_report.get("provided") or not template_report.get("exists"):
        raise RuntimeError("host preflight did not verify a drawing template")
    reported = template_report.get("path")
    if reported and Path(reported).resolve() != template:
        raise RuntimeError("host preflight verified a different drawing template")


def _require_new_publication_directory(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise FileExistsError(f"publication directory must be new or empty: {path}")


def _require_new_output(output: Path, publication: Path) -> None:
    if output.suffix.lower() != ".slddrw":
        raise ValueError("output path must end with .SLDDRW")
    if output.exists() or Path(str(output) + ".verification.json").exists():
        raise FileExistsError(f"output path collision: {output}")
    if output.parent != publication:
        raise ValueError("output drawing must be inside the fresh publication directory")


def _handoff_files(manifest: dict[str, Any]) -> list[Path]:
    paths = [
        Path(manifest["blank_drawing"]["path"]),
        Path(manifest["readiness_report"]["path"]),
        Path(manifest["geometry_report"]["path"]),
    ]
    paths.extend(Path(row["path"]) for row in manifest["standard_view_images"])
    return paths


def _files_under(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _snapshot(paths: Iterable[Path]) -> dict[str, str]:
    return {str(path.resolve(strict=True)): _sha(path) for path in sorted(set(paths))}


def _process_image(process_id: int) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("runtime ownership verification requires Windows")
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


def _loopback_address(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("execution base URL must be an HTTP loopback origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port is None:
        raise ValueError("execution base URL must contain only an explicit loopback port")
    return parsed.hostname, parsed.port


def _require_port_free(base_url: str) -> None:
    host, port = _loopback_address(base_url)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((host, port)) == 0:
            raise RuntimeError(f"execution port is already occupied: {base_url}")


def _wait_for_port(base_url: str, process: subprocess.Popen[str]) -> None:
    host, port = _loopback_address(base_url)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"execution runtime exited before binding its port: {process.returncode}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((host, port)) == 0:
                return
        time.sleep(0.25)
    raise TimeoutError("execution runtime did not bind its loopback port")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha(path)}


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
