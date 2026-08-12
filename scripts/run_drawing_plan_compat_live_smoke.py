"""Run the explicit DrawingPlan 1.0 compatibility MCP against real SolidWorks.

The smoke test owns both child processes it starts, writes only beneath a new output
directory, and proves that the repository validation fixtures remain byte-for-byte unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


_TOOLS = (
    "validate_part_drawing_plan",
    "create_part_drawing",
    "verify_part_drawing",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--validation-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--execution-exe", required=True)
    args = parser.parse_args()
    try:
        report = run_smoke(
            Path(args.repository_root),
            Path(args.validation_dir),
            Path(args.output_dir),
            Path(args.execution_exe),
        )
    except Exception as exc:  # stable CLI failure for release automation
        print(f"DrawingPlan compatibility live smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


def run_smoke(
    repository_root: Path,
    validation_dir: Path,
    output_dir: Path,
    execution_exe: Path,
) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    validation = validation_dir.resolve(strict=True)
    output = output_dir.resolve()
    executable = execution_exe.resolve(strict=True)
    if validation == output or validation in output.parents:
        raise ValueError("smoke output must not be inside validation_dir")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("smoke output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(root))
    from drawing_planner.validation_matrix import snapshot_validation_tree

    before = snapshot_validation_tree(validation)
    model_path = _single(validation, ".sldprt")
    template_path = _single(validation, ".drwdot")
    source_plan = _single(validation, ".json", name="drawing-plan.validation.json")
    drawing_path = output / "drawing-plan-compat-live.SLDDRW"
    plan = json.loads(source_plan.read_text(encoding="utf-8"))
    plan["model"]["path"] = str(model_path)
    plan["drawing"]["template_path"] = str(template_path)
    plan["drawing"]["output_path"] = str(drawing_path)
    plan["drawing"]["overwrite"] = False
    plan_path = output / "drawing-plan.compat.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    stdout_path = output / "execution-server.stdout.log"
    stderr_path = output / "execution-server.stderr.log"
    mcp_stderr_path = output / "compat-mcp.stderr.log"
    process: subprocess.Popen[str] | None = None
    started = time.time()
    responses: dict[str, Any] = {}
    readiness: dict[str, Any] = {}
    state_before = 0
    state_after = 0
    with stdout_path.open("w", encoding="utf-8") as server_stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as server_stderr:
        try:
            if _service_is_up():
                raise RuntimeError(
                    "port 5000 already has an execution service; smoke cannot prove ownership"
                )
            process = subprocess.Popen(
                [str(executable)],
                cwd=executable.parent,
                stdout=server_stdout,
                stderr=server_stderr,
                text=True,
                creationflags=0x08000000,
            )
            _wait_until_up(process)
            with httpx.Client(
                base_url="http://localhost:5000", trust_env=False, timeout=180
            ) as client:
                ready_response = client.post("/ensure_ready")
                ready_response.raise_for_status()
                readiness = ready_response.json()
                if not readiness.get("comAttached"):
                    raise RuntimeError("execution service did not attach to SolidWorks")
                state_before = int(client.get("/api/tool/state").json()["stateVersion"])

            with mcp_stderr_path.open("w", encoding="utf-8") as mcp_stderr:
                responses = asyncio.run(
                    _call_compat_mcp(root, plan, mcp_stderr)
                )

            with httpx.Client(
                base_url="http://localhost:5000", trust_env=False, timeout=10
            ) as client:
                state_after = int(client.get("/api/tool/state").json()["stateVersion"])
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    sidecar_path = Path(str(drawing_path) + ".verification.json")
    after = snapshot_validation_tree(validation)
    unchanged = before["tree_sha256"] == after["tree_sha256"]
    validate = responses.get("validate_part_drawing_plan", {})
    create = responses.get("create_part_drawing", {})
    verify = responses.get("verify_part_drawing", {})
    passed = all(
        (
            unchanged,
            validate.get("ok") is True,
            validate.get("schema_version") == "1.0",
            create.get("ok") is True,
            create.get("status") == "COMPLETED",
            create.get("verified") is True,
            verify.get("ok") is True,
            verify.get("status") == "COMPLETED",
            verify.get("verified") is True,
            state_after == state_before + 1,
            create.get("state_version") == state_after,
            verify.get("state_version") == state_after,
            drawing_path.is_file(),
            sidecar_path.is_file(),
        )
    )
    report = {
        "schema_version": "1.0",
        "status": "pass" if passed else "fail",
        "transport": "stdio-mcp",
        "mcp_tools": list(_TOOLS),
        "solidworks_revision": readiness.get("swVersion"),
        "execution_runtime": str(executable),
        "execution_runtime_sha256": _sha(executable),
        "state_version": {"before": state_before, "after": state_after},
        "responses": responses,
        "artifacts": {
            "plan": _artifact(plan_path),
            "drawing": _artifact(drawing_path),
            "verification_sidecar": _artifact(sidecar_path),
        },
        "validation_inputs": {"unchanged": unchanged, "before": before, "after": after},
        "duration_seconds": round(time.time() - started, 3),
    }
    report_path = output / "drawing-plan-compat-live-smoke.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


async def _call_compat_mcp(root: Path, plan: dict, errlog) -> dict[str, Any]:
    params = StdioServerParameters(
        command=str(root / ".venv/Scripts/python.exe"),
        args=[str(root / "adapters/claude/drawing_plan_compat_server.py")],
        cwd=str(root),
        env=os.environ.copy(),
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    responses: dict[str, Any] = {}
    async with stdio_client(params, errlog=errlog) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = tuple(tool.name for tool in tools.tools)
            if set(names) != set(_TOOLS) or len(names) != len(_TOOLS):
                raise RuntimeError(f"unexpected compatibility MCP tools: {names}")
            for name in _TOOLS:
                result = await session.call_tool(
                    name,
                    {"plan": plan},
                    read_timeout_seconds=timedelta(seconds=240),
                )
                if result.isError:
                    raise RuntimeError(f"compatibility MCP tool failed: {name}")
                text_blocks = [
                    block.text for block in result.content if block.type == "text"
                ]
                if len(text_blocks) != 1:
                    raise RuntimeError(f"{name} returned {len(text_blocks)} text blocks")
                responses[name] = json.loads(text_blocks[0])
    return responses


def _service_is_up() -> bool:
    try:
        with httpx.Client(trust_env=False) as client:
            return client.get("http://localhost:5000/health", timeout=1).status_code == 200
    except Exception:
        return False


def _wait_until_up(process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"execution service exited before health became ready: {process.returncode}"
            )
        if _service_is_up():
            return
        time.sleep(0.25)
    raise TimeoutError("execution service did not become healthy within 30 seconds")


def _single(root: Path, suffix: str, *, name: str | None = None) -> Path:
    matches = [
        path
        for path in root.iterdir()
        if path.is_file()
        and path.suffix.lower() == suffix
        and (name is None or path.name == name)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name or suffix} fixture, found {len(matches)}")
    return matches[0].resolve(strict=True)


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha(path)}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
