"""Deterministic, COM-free H0 five-Skill release-readiness audit.

This gate deliberately does not run a qualification transaction.  It proves that the
repository is eligible to start the production H0 live chain and fails closed while an
upstream production capability is still merely planned.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from dimension_planner.capability_registry import (
    DimensionCapabilityManifest,
)
from drawing_layout_planner.capability_registry import (
    DrawingLayoutCapabilityManifest,
)


PROTOCOL_ID = "solidworks-five-skill-release-readiness"
SCHEMA_VERSION = "1.0"

_SKILL_ORDER = (
    "bootstrap-solidworks-host",
    "solidworks-initialize-drawing-handoff",
    "solidworks-create-drawing-views",
    "solidworks-dimension-drawing",
    "solidworks-finalize-drawing-layout",
)
_PLAN_SCHEMAS = (
    "drawing_planner/contracts/view-plan.schema.json",
    "dimension_planner/contracts/dimension-plan.schema.json",
    "drawing_layout_planner/contracts/drawing-layout-plan.schema.json",
)
_DIMENSION_EXECUTION_ELEMENTS = (
    "model_dimension_import",
    "attachment_persistent_reference",
    "annotation_position",
    "dimension_tolerance",
    "dimension_prefix_suffix",
    "save_reopen_stable_identity",
)


def audit_h0_readiness(repository_root: Path) -> dict[str, Any]:
    """Return one schema-valid H0 readiness report without starting SolidWorks."""

    root = repository_root.resolve(strict=True)
    blockers: list[dict[str, Any]] = []
    contract_path = root / "adapters/claude/contracts/skill-chain.contract.json"
    semantic_schema_path = root / "adapters/claude/contracts/semantic-tools.schema.json"
    config_path = root / ".codex/config.toml"
    contract = _load_json(contract_path)
    semantic_schema = _load_json(semantic_schema_path)
    config = _load_toml(config_path)

    expected_tools = contract.get("default_mcp", {}).get("tools")
    if not isinstance(expected_tools, list) or not all(
        isinstance(name, str) and name for name in expected_tools
    ):
        raise ValueError("five-Skill contract has no valid default MCP tool inventory")
    config_tools = (
        config.get("mcp_servers", {}).get("solidpilot", {}).get("enabled_tools")
    )
    schema_tools = semantic_schema.get("required")
    property_tools = list(semantic_schema.get("properties", {}))
    if not (
        expected_tools == config_tools == schema_tools == property_tools
        and len(expected_tools) == 24
        and len(set(expected_tools)) == 24
    ):
        _block(
            blockers,
            "semantic-surface-drift",
            "the contract, Codex configuration and semantic schema must expose the same 24 tools",
            contract_path,
            config_path,
            semantic_schema_path,
        )

    skill_rows = _audit_skills(root, contract, blockers)
    schema_rows = [_artifact(root / relative) for relative in _PLAN_SCHEMAS]
    for row in schema_rows:
        _validate_schema_document(Path(row["path"]), blockers)

    view_path = root / "drawing_planner/capabilities/current.json"
    dimension_path = root / "dimension_planner/capabilities/current.json"
    layout_boundary_path = root / "drawing_layout_planner/capabilities/current.json"
    layout_plan_path = root / "drawing_layout_planner/capabilities/plan-current.json"
    view = _load_json(view_path)
    dimension_raw = _load_json(dimension_path)
    layout_boundary = _load_json(layout_boundary_path)
    layout_plan_raw = _load_json(layout_plan_path)

    _audit_view_capabilities(view, view_path, blockers)
    dimension = DimensionCapabilityManifest.model_validate(dimension_raw)
    _audit_dimension_capabilities(dimension, dimension_path, blockers)
    layout_plan = DrawingLayoutCapabilityManifest.model_validate(layout_plan_raw)
    _audit_layout_capabilities(
        layout_boundary,
        layout_plan,
        layout_boundary_path,
        layout_plan_path,
        blockers,
    )

    actual_tools, prompt_count = _discover_default_surface(root)
    if actual_tools != expected_tools or prompt_count != 0:
        _block(
            blockers,
            "live-semantic-surface-drift",
            "actual FastMCP discovery must match the frozen 24-tool inventory and expose zero prompts",
            semantic_schema_path,
        )

    git = _git_state(root)
    if not git["clean"]:
        _block(
            blockers,
            "git-worktree-not-frozen",
            "H0 evidence must be generated from one clean, immutable commit",
            *git["changed_paths"],
        )

    capability_rows = {
        "view": _artifact(view_path),
        "dimension": _artifact(dimension_path),
        "layout_boundary": _artifact(layout_boundary_path),
        "layout_plan": _artifact(layout_plan_path),
    }
    report: dict[str, Any] = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if not blockers else "blocked",
        "git": git,
        "semantic_mcp": {
            "tool_count": len(actual_tools),
            "tools": actual_tools,
            "prompt_count": prompt_count,
            "contract": _artifact(contract_path),
            "config": _artifact(config_path),
            "schema": _artifact(semantic_schema_path),
        },
        "skills": skill_rows,
        "plan_schemas": schema_rows,
        "capability_manifests": capability_rows,
        "blockers": blockers,
    }
    _validate_report(root, report)
    return report


def _audit_skills(
    root: Path, contract: Mapping[str, Any], blockers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    stages = contract.get("stages")
    if not isinstance(stages, list) or tuple(
        stage.get("skill") for stage in stages if isinstance(stage, Mapping)
    ) != _SKILL_ORDER:
        _block(
            blockers,
            "skill-order-drift",
            "the production contract must preserve the five-Skill order",
            root / "adapters/claude/contracts/skill-chain.contract.json",
        )
        return []
    rows: list[dict[str, Any]] = []
    for stage in stages:
        path = root / str(stage["path"])
        text = path.read_text(encoding="utf-8")
        match = re.search(
            r"^## Allowed semantic tools\n\n(?P<body>.*?)(?=^## |\Z)",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        actual = (
            re.findall(r"^- `([a-z0-9_]+)`$", match.group("body"), re.MULTILINE)
            if match
            else []
        )
        if actual != stage.get("allowed_tools"):
            _block(
                blockers,
                "skill-allow-list-drift",
                f"{stage['skill']} does not match its frozen semantic allow-list",
                path,
            )
        rows.append({"name": stage["skill"], **_artifact(path)})
    return rows


def _audit_view_capabilities(
    manifest: Mapping[str, Any], path: Path, blockers: list[dict[str, Any]]
) -> None:
    if manifest.get("executor_version") != "1.0.0":
        _block(
            blockers,
            "view-capability-baseline-not-promoted",
            "the ViewPlan production executor must remain at the live 1.0.0 baseline",
            path,
        )
    for catalog_name in ("view_types", "elements"):
        catalog = manifest.get(catalog_name)
        if not isinstance(catalog, Mapping) or any(
            not isinstance(row, Mapping)
            or row.get("status") != "supported"
            or row.get("verification") != "live"
            for row in catalog.values()
        ):
            _block(
                blockers,
                "view-capability-not-live-supported",
                f"every {catalog_name} entry required by the H0 baseline must be live-supported",
                path,
            )


def _audit_dimension_capabilities(
    manifest: DimensionCapabilityManifest,
    path: Path,
    blockers: list[dict[str, Any]],
) -> None:
    missing_types = sorted(
        name for name, entry in manifest.dimension_types.items() if entry.status != "supported"
    )
    missing_elements = sorted(
        name
        for name in _DIMENSION_EXECUTION_ELEMENTS
        if manifest.elements[name].status != "supported"
    )
    if manifest.live_evidence is None or missing_types or missing_elements:
        references = [f"dimension_type.{name}" for name in missing_types]
        references.extend(f"element.{name}" for name in missing_elements)
        _block(
            blockers,
            "f7-production-capabilities-not-promoted",
            "H0 cannot use the production dimension transaction until F7 promotes all 18 kinds and six execution elements",
            path,
            *references,
        )


def _audit_layout_capabilities(
    boundary: Mapping[str, Any],
    plan: DrawingLayoutCapabilityManifest,
    boundary_path: Path,
    plan_path: Path,
    blockers: list[dict[str, Any]],
) -> None:
    boundary_entries = boundary.get("capabilities")
    if (
        boundary.get("verification") != "live_complete"
        or not isinstance(boundary_entries, list)
        or any(row.get("status") != "supported" for row in boundary_entries)
    ):
        _block(
            blockers,
            "g0-boundaries-not-live-supported",
            "all exact layout boundaries must be live-supported before H0",
            boundary_path,
        )
    if any(entry.status != "supported" for entry in plan.operations.values()) or any(
        entry.status != "supported" for entry in plan.safety_elements.values()
    ):
        _block(
            blockers,
            "g7-production-capabilities-not-promoted",
            "all layout operations and safety readbacks must be live-supported before H0",
            plan_path,
        )
    if plan.boundary_registry.manifest_sha256 != _sha256(boundary_path):
        _block(
            blockers,
            "layout-boundary-binding-drift",
            "the layout plan registry must bind the exact current G0 boundary manifest",
            boundary_path,
            plan_path,
        )


def _discover_default_surface(root: Path) -> tuple[list[str], int]:
    adapter = str(root / "adapters/claude")
    if adapter not in sys.path:
        sys.path.insert(0, adapter)
    from adapters.claude import server

    async def discover() -> tuple[list[str], int]:
        tools = await server.mcp.list_tools()
        prompts = await server.mcp.list_prompts()
        return [tool.name for tool in tools], len(prompts)

    return asyncio.run(discover())


def _git_state(root: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    changed = sorted(line[3:] for line in status if len(line) > 3)
    return {"commit": commit, "clean": not status, "changed_paths": changed}


def _validate_schema_document(path: Path, blockers: list[dict[str, Any]]) -> None:
    try:
        Draft202012Validator.check_schema(_load_json(path))
    except Exception as exc:
        _block(
            blockers,
            "plan-schema-invalid",
            f"Draft 2020-12 schema validation failed: {exc}",
            path,
        )


def _validate_report(root: Path, report: Mapping[str, Any]) -> None:
    schema = _load_json(root / "release_candidate/contracts/h0-readiness.schema.json")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise ValueError("H0 readiness report violates its schema: " + errors[0].message)


def _block(
    blockers: list[dict[str, Any]],
    code: str,
    message: str,
    *references: object,
) -> None:
    blockers.append(
        {
            "code": code,
            "message": message,
            "references": [str(reference) for reference in references],
        }
    )


def _artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)
