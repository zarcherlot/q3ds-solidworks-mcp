"""Repository-owned D1 live ViewPlan matrix over the private C# transaction."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_VIEWS = ("front", "back", "left", "right", "top", "bottom")
_PRODUCER = {
    "name": "q3ds-repository-planner",
    "version": "2.0.0",
    "ruleset_id": "baseline-2.0.0",
    "ruleset_sha256": "f29009bf1a7df333711db555f9a40471b26217ad6c1cda46c75100bce4306285",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--validation-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--execution-exe", required=True)
    args = parser.parse_args()
    try:
        report = run_live_matrix(
            Path(args.repository_root),
            Path(args.validation_dir),
            Path(args.output_dir),
            Path(args.execution_exe),
        )
    except Exception as exc:  # report setup/runtime failures with a stable CLI code
        print(f"D1 live matrix failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


def run_live_matrix(
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
        raise ValueError("live output must not be inside validation_dir")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("live output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(root))
    from drawing_planner.validation_matrix import snapshot_validation_tree

    before = snapshot_validation_tree(validation)
    model_path = _single(validation, ".sldprt")
    drawing_path = _single(validation, ".slddrw")
    base_fixture = json.loads(
        (root / "drawing_planner/tests/fixtures/view_plan.valid.json").read_text(
            encoding="utf-8"
        )
    )
    server_stdout = (output / "execution-server.stdout.log").open(
        "w", encoding="utf-8"
    )
    server_stderr = (output / "execution-server.stderr.log").open(
        "w", encoding="utf-8"
    )
    process: subprocess.Popen[str] | None = None
    started = time.time()
    rows: list[dict[str, Any]] = []
    service = _ExecutionService()
    try:
        if service.is_up():
            raise RuntimeError(
                "port 5000 already has an execution service; D1 cannot prove runtime ownership"
            )
        process = subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            stdout=server_stdout,
            stderr=server_stderr,
            text=True,
            creationflags=0x08000000,
        )
        service.wait_until_up(process)
        readiness = service.ensure_ready()
        if not readiness.get("comAttached"):
            raise RuntimeError("execution service did not attach to SolidWorks")
        context = service.call(
            "inspect_part_for_drawing", {"model_path": str(model_path)}, mutating=False
        )
        geometry = context.get("result_geometry") or {}
        configuration = geometry.get("configuration")
        if not isinstance(configuration, str) or not configuration:
            raise RuntimeError("read-only model inspection returned no configuration")

        for case_id in _CASE_IDS:
            rows.append(
                _run_case(
                    root,
                    output,
                    model_path,
                    drawing_path,
                    base_fixture,
                    configuration,
                    case_id,
                    service,
                )
            )
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        server_stdout.close()
        server_stderr.close()
        service.close()

    after = snapshot_validation_tree(validation)
    unchanged = before["tree_sha256"] == after["tree_sha256"]
    passed = bool(rows) and all(row["status"] == "pass" for row in rows) and unchanged
    report = {
        "schema_version": "1.0",
        "status": "pass" if passed else "fail",
        "solidworks_revision": readiness.get("swVersion"),
        "execution_runtime": str(executable),
        "execution_runtime_sha256": _sha(executable),
        "validation_inputs": {
            "unchanged": unchanged,
            "before": before,
            "after": after,
        },
        "save_close_read_only_reopen_verified": True,
        "independent_verification_verified": True,
        "new_output_only": True,
        "case_count": len(rows),
        "duration_seconds": round(time.time() - started, 3),
        "cases": rows,
    }
    report_path = output / "view-plan-live-matrix.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


class _ExecutionService:
    def __init__(self) -> None:
        self._client = httpx.Client(base_url="http://localhost:5000", trust_env=False)
        self.state_version = 0

    def close(self) -> None:
        self._client.close()

    def is_up(self) -> bool:
        try:
            return self._client.get("/health", timeout=1).status_code == 200
        except Exception:
            return False

    def wait_until_up(self, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"execution service exited before health became ready: {process.returncode}"
                )
            if self.is_up():
                health = self._client.get("/health", timeout=2).json()
                self.state_version = int(health.get("stateVersion", 0))
                return
            time.sleep(0.25)
        raise TimeoutError("execution service did not become healthy within 30 seconds")

    def ensure_ready(self) -> dict[str, Any]:
        response = self._client.post("/ensure_ready", timeout=180)
        response.raise_for_status()
        return response.json()

    def call(
        self, tool: str, params: dict[str, Any], *, mutating: bool
    ) -> dict[str, Any]:
        payload = {
            "operationId": f"d1-{tool}-{uuid.uuid4()}",
            "tool": tool,
            "stateVersion": self.state_version,
            "params": params,
        }
        response = self._client.post("/api/tool/execute", json=payload, timeout=240)
        response.raise_for_status()
        body = response.json()
        if body.get("status") == "FAILED" and (body.get("error") or {}).get(
            "code"
        ) == "INVALID_STATE_VERSION":
            state = self._client.get("/api/tool/state", timeout=10).json()
            self.state_version = int(state["stateVersion"])
            payload["operationId"] = f"d1-{tool}-{uuid.uuid4()}"
            payload["stateVersion"] = self.state_version
            response = self._client.post(
                "/api/tool/execute", json=payload, timeout=240
            )
            response.raise_for_status()
            body = response.json()
        if mutating and body.get("status") == "COMPLETED":
            returned = body.get("stateVersion")
            if not isinstance(returned, int) or returned <= self.state_version:
                raise RuntimeError(f"{tool} returned an invalid stateVersion")
            self.state_version = returned
        return body


_CASE_IDS = (
    "basic_projected",
    "full_section",
    "half_section",
    "offset_section",
    "aligned_section",
    "removed_section",
    "broken_out_section",
    "detail_view",
    "detail_view_jagged",
    "detail_view_explicit",
    "auxiliary_aligned",
    "auxiliary_free_flipped_explicit",
    "center_elements",
)


def _run_case(
    root: Path,
    output: Path,
    model_path: Path,
    drawing_path: Path,
    base_fixture: dict[str, Any],
    configuration: str,
    case_id: str,
    service: _ExecutionService,
) -> dict[str, Any]:
    case_root = output / case_id
    case_root.mkdir()
    blank = case_root / "ready-blank.SLDDRW"
    blank.write_bytes(drawing_path.read_bytes())
    geometry = case_root / "model-geometry.json"
    geometry.write_text(
        json.dumps(_geometry_report(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    readiness = case_root / "drawing-readiness.json"
    readiness.write_text('{"schema_version":1,"ready":true}\n', encoding="utf-8")
    images = {}
    for view in _VIEWS:
        path = case_root / f"{view}.png"
        path.write_bytes(_PNG)
        images[view] = path

    plan = _base_plan(
        base_fixture,
        model_path,
        blank,
        geometry,
        readiness,
        images,
        configuration,
        case_id,
    )
    _configure_case(plan, case_id, geometry)
    plan_path = case_root / "view_plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_path = case_root / f"{case_id}.SLDDRW"

    validation = service.call(
        "validate_frozen_part_drawing_view_plan", {"plan": plan}, mutating=False
    )
    _require_completed(validation, f"{case_id} COM-free validation")
    validation_geometry = validation.get("result_geometry") or {}
    if validation_geometry.get("solidworks_contacted") is not False:
        raise RuntimeError(f"{case_id} validation was not COM-free")
    created = service.call(
        "execute_part_drawing_view_plan",
        {"plan": plan, "output_path": str(output_path)},
        mutating=True,
    )
    _require_completed(created, f"{case_id} create")
    create_geometry = created.get("result_geometry") or {}
    memory = _snapshot(create_geometry, "in_memory_verification", case_id)
    reopen = _snapshot(create_geometry, "reopen_verification", case_id)
    if memory != reopen:
        raise RuntimeError(f"{case_id} normalized fingerprint changed after reopen")
    verified = service.call(
        "verify_committed_part_drawing_view_plan",
        {"plan": plan, "output_path": str(output_path)},
        mutating=False,
    )
    _require_completed(verified, f"{case_id} independent verify")
    independent = _snapshot(
        verified.get("result_geometry") or {}, "verification", case_id
    )
    if reopen != independent:
        raise RuntimeError(f"{case_id} independent fingerprint differs")
    sidecar = Path(str(output_path) + ".verification.json")
    if not output_path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"{case_id} did not commit its drawing and sidecar")
    return {
        "case_id": case_id,
        "status": "pass",
        "plan_path": str(plan_path),
        "output_path": str(output_path),
        "verification_report": str(sidecar),
        "artifact_sha256": _sha(output_path),
        "sidecar_sha256": _sha(sidecar),
        "state_version": service.state_version,
        "fingerprint": reopen,
    }


def _base_plan(
    fixture: dict[str, Any],
    model: Path,
    drawing: Path,
    geometry: Path,
    readiness: Path,
    images: dict[str, Path],
    configuration: str,
    case_id: str,
) -> dict[str, Any]:
    plan = copy.deepcopy(fixture)
    plan["plan_id"] = f"VP-D1-LIVE-{case_id.upper()}"
    plan["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    plan["producer"] = copy.deepcopy(_PRODUCER)
    plan["model_path"] = str(model)
    plan["model_sha256"] = _sha(model)
    plan["drawing_path"] = str(drawing)
    plan["drawing_sha256"] = _sha(drawing)
    plan["geometry_report_path"] = str(geometry)
    plan["geometry_report_sha256"] = _sha(geometry)
    plan["readiness_report_path"] = str(readiness)
    plan["readiness_report_sha256"] = _sha(readiness)
    plan["configuration"] = configuration
    plan["display_state"] = None
    plan["sheet"] = {
        "name": "Model",
        "format_name": "A3-Landscape",
        "width_m": 0.42,
        "height_m": 0.297,
    }
    plan["projection_method"] = "third_angle"
    plan["sheet_scale"] = {"numerator": 1, "denominator": 5}
    for row in plan["standard_view_images"]:
        path = images[row["view"]]
        row["path"] = str(path)
        row["sha256"] = _sha(path)
    _rewrite_evidence(plan, geometry)
    return plan


def _configure_case(plan: dict[str, Any], case_id: str, geometry: Path) -> None:
    parent = plan["views"][0]
    child = plan["views"][1]
    _configure_parent(parent)
    if case_id == "basic_projected":
        _configure_projected(child)
    elif case_id in {
        "full_section",
        "half_section",
        "offset_section",
        "aligned_section",
        "removed_section",
    }:
        _configure_section(child, case_id)
    elif case_id == "broken_out_section":
        _configure_broken_out(child, parent)
    elif case_id.startswith("detail_view"):
        _configure_detail(child, case_id)
    elif case_id.startswith("auxiliary_"):
        _configure_auxiliary(child, case_id)
    elif case_id == "center_elements":
        _configure_center_elements(plan, parent, geometry)
        return
    else:
        raise ValueError(f"unknown D1 live case: {case_id}")
    plan["views"] = [parent, child]


def _configure_parent(parent: dict[str, Any]) -> None:
    parent["id"] = "front"
    parent["type"] = "model_view"
    parent["source"] = {"kind": "model_document", "reference": "model"}
    parent["orientation"] = {
        "kind": "standard_model_view",
        "standard_view": "front",
        "roll_angle_rad": 0.0,
    }
    parent["parent_view_id"] = None
    parent["alignment"] = "none"
    parent["scale"] = 0.2
    parent["position_sheet_m"] = [0.12, 0.16]
    parent["placement_box"] = _box(0.055, 0.095, 0.185, 0.225)
    parent["center_marks"] = []
    parent["symmetry_centerlines"] = []
    parent["section_definition"] = None
    parent["broken_out_definition"] = None
    parent["detail_definition"] = None
    parent["auxiliary_definition"] = None
    parent["label"] = None


def _configure_projected(child: dict[str, Any]) -> None:
    child.update(
        {
            "id": "projected-d1",
            "type": "projected_view",
            "source": {
                "kind": "parent_view",
                "reference": "front",
                "projection_direction": "right",
            },
            "orientation": {"kind": "derived_from_parent"},
            "parent_view_id": "front",
            "alignment": "projected",
            "scale": 0.2,
            "position_sheet_m": [0.29, 0.16],
            "placement_box": _box(0.225, 0.095, 0.355, 0.225),
            "center_marks": [],
            "symmetry_centerlines": [],
            "section_definition": None,
            "broken_out_definition": None,
            "detail_definition": None,
            "auxiliary_definition": None,
            "label": None,
        }
    )


def _configure_section(child: dict[str, Any], kind: str) -> None:
    points = {
        "half_section": [[-0.0767, 0.004, 0], [0, 0.004, 0], [0, 0.0132, 0]],
        "offset_section": [
            [-0.0767, 0.004, 0],
            [0, 0.004, 0],
            [0, 0.014, 0],
            [0.0767, 0.014, 0],
        ],
        "aligned_section": [
            [-0.0767, -0.0012, 0],
            [0, 0.004, 0],
            [0.0767, -0.0012, 0],
        ],
        "removed_section": [[-0.0767, 0.004, 0], [0.0767, 0.004, 0]],
    }
    modes = {
        "half_section": "explicit_half",
        "offset_section": "explicit_offset",
        "aligned_section": "explicit_aligned",
        "removed_section": "explicit_removed",
    }
    definition = {
        "cutting_plane_mode": "through_feature_axes"
        if kind == "full_section"
        else modes[kind],
        "feature_ids": ["B0F0"],
        "cutting_line_points_model_m": []
        if kind == "full_section"
        else points[kind],
        "cutting_line_axis": "vertical" if kind == "full_section" else None,
        "line_extension_ratio": 0.1 if kind == "full_section" else None,
        "reverse_direction": kind == "removed_section",
        "section_depth_m": 0,
    }
    child.update(
        {
            "id": f"{kind}-d1",
            "type": kind,
            "source": {
                "kind": "parent_view",
                "reference": "front",
                "projection_direction": None,
            },
            "orientation": {"kind": "derived_from_parent"},
            "parent_view_id": "front",
            "alignment": "not_aligned"
            if kind in {"aligned_section", "removed_section"}
            else "projected",
            "scale": 0.2,
            "position_sheet_m": [0.12, 0.255]
            if kind == "offset_section"
            else [0.29, 0.16],
            "placement_box": _box(0.06, 0.225, 0.18, 0.285)
            if kind == "offset_section"
            else _box(0.225, 0.095, 0.355, 0.225),
            "center_marks": [],
            "symmetry_centerlines": [],
            "section_definition": definition,
            "broken_out_definition": None,
            "detail_definition": None,
            "auxiliary_definition": None,
            "label": {
                "text": {
                    "full_section": "A",
                    "half_section": "B",
                    "offset_section": "C",
                    "aligned_section": "D",
                    "removed_section": "E",
                }[kind],
                "show": True,
                "position_mode": "document_default",
            },
        }
    )


def _configure_broken_out(child: dict[str, Any], parent: dict[str, Any]) -> None:
    child.update(
        {
            "id": "broken-d1",
            "type": "broken_out_section",
            "source": copy.deepcopy(parent["source"]),
            "orientation": copy.deepcopy(parent["orientation"]),
            "parent_view_id": None,
            "alignment": "none",
            "scale": 0.2,
            "position_sheet_m": [0.29, 0.16],
            "placement_box": _box(0.225, 0.095, 0.355, 0.225),
            "center_marks": [],
            "symmetry_centerlines": [],
            "section_definition": {
                "cutting_plane_mode": "explicit_broken_out",
                "feature_ids": ["B0F0"],
                "cutting_line_points_model_m": [],
                "cutting_line_axis": None,
                "line_extension_ratio": None,
                "reverse_direction": False,
                "section_depth_m": 0,
            },
            "broken_out_definition": {
                "base_view_mode": "model_orientation",
                "boundary_mode": "circle",
                "center_offset_from_view_m": [0.0, 0.0],
                "radius_sheet_m": 0.007,
                "depth_m": 0.004,
            },
            "detail_definition": None,
            "auxiliary_definition": None,
            "label": None,
        }
    )


def _configure_detail(child: dict[str, Any], case_id: str) -> None:
    explicit = case_id == "detail_view_explicit"
    jagged = case_id == "detail_view_jagged"
    label = {"text": "C-C", "show": True, "position_mode": "explicit" if explicit else "document_default"}
    if explicit:
        label["position_sheet_m"] = [0.122397701067, 0.167376551497]
    child.update(
        {
            "id": f"{case_id}-d1",
            "type": "detail_view",
            "source": {
                "kind": "parent_view",
                "reference": "front",
                "projection_direction": None,
            },
            "orientation": {"kind": "derived_from_parent"},
            "parent_view_id": "front",
            "alignment": "none",
            "scale": 0.4,
            "position_sheet_m": [0.29, 0.16],
            "placement_box": _box(0.225, 0.095, 0.355, 0.225),
            "center_marks": [],
            "symmetry_centerlines": [],
            "section_definition": None,
            "broken_out_definition": None,
            "detail_definition": {
                "profile_mode": "circle",
                "center_offset_from_parent_m": [0.0, 0.0],
                "radius_sheet_m": 0.007,
                "style": "standard",
                "show_type": "profile",
                "full_outline": True,
                "jagged_outline": jagged,
                "no_outline": False,
                "shape_intensity": 3,
            },
            "auxiliary_definition": None,
            "label": label,
        }
    )


def _configure_auxiliary(child: dict[str, Any], case_id: str) -> None:
    explicit = case_id.endswith("explicit")
    child.update(
        {
            "id": f"{case_id}-d1",
            "type": "auxiliary_view",
            "source": {
                "kind": "parent_view",
                "reference": "front",
                "projection_direction": None,
            },
            "orientation": {"kind": "derived_from_parent"},
            "parent_view_id": "front",
            "alignment": "not_aligned" if explicit else "projected",
            "scale": 0.2,
            "position_sheet_m": [0.29, 0.16] if explicit else [0.12, 0.06],
            "placement_box": _box(0.235, 0.105, 0.345, 0.215)
            if explicit
            else _box(0.065, 0.005, 0.175, 0.115),
            "center_marks": [],
            "symmetry_centerlines": [],
            "section_definition": None,
            "broken_out_definition": None,
            "detail_definition": None,
            "auxiliary_definition": {
                "reference_edge_start_model_m": [-0.059, 0.0005, 0.013],
                "reference_edge_end_model_m": [0.059, 0.0005, 0.013],
                "match_tolerance_sheet_m": 0.00001,
                "not_aligned": explicit,
                "show_arrow": True,
                "flip": explicit,
            },
            "label": {
                "text": "B" if explicit else "A",
                "show": True,
                "position_mode": "explicit" if explicit else "document_default",
            },
        }
    )
    if explicit:
        child["label"]["position_sheet_m"] = [0.10529098974017003, 0.181980319227]


def _configure_center_elements(
    plan: dict[str, Any], parent: dict[str, Any], geometry: Path
) -> None:
    parent["orientation"] = {
        "kind": "standard_model_view",
        "standard_view": "bottom",
        "roll_angle_rad": 0.0,
    }
    parent["scale"] = 1.0
    parent["position_sheet_m"] = [0.21, 0.1485]
    parent["placement_box"] = _box(0.05, 0.05, 0.37, 0.247)
    parent["expressed_features"] = ["B0F18", "B0F20"]
    parent["model_evidence"] = [
        {
            "report_path": str(geometry),
            "json_pointer": "/bodies/0/faces/1",
            "finding": "Frozen circular hole features and symmetric outline support D1 live verification.",
        }
    ]
    parent["center_marks"] = [
        {
            "id": "cm-bottom-holes",
            "feature_ids": ["B0F18", "B0F20"],
            "selection_strategy": "visible_closed_circular_edges_by_feature",
            "deduplicate_by": "projected_center",
            "expected_count": 2,
            "style": "linear_group",
            "use_document_defaults": False,
            "show_lines": False,
            "propagate": False,
            "slot": False,
            "color_rgb": [255, 0, 0],
        }
    ]
    parent["symmetry_centerlines"] = [
        _centerline("cl-horizontal", "horizontal", geometry),
        _centerline("cl-vertical", "vertical", geometry),
    ]
    plan["views"] = [parent]
    plan["main_view_id"] = "front"
    plan["decision_summary"]["final_minimum_view_set"] = [
        {
            "view_id": "front",
            "omission_impact": "Center-element contract would be lost.",
        }
    ]


def _centerline(identifier: str, axis: str, geometry: Path) -> dict[str, Any]:
    return {
        "id": identifier,
        "axis": axis,
        "selection_strategy": "opposed_visible_linear_edges",
        "minimum_edge_span_ratio": 0.6,
        "purpose": "Persisted symmetry datum for D1 live verification.",
        "color_rgb": [255, 0, 0],
        "model_evidence": [
            {
                "report_path": str(geometry),
                "json_pointer": "/part_box_m",
                "finding": "The frozen part box is symmetric about this sheet axis.",
            }
        ],
    }


def _geometry_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "success",
        "source": "repository D1 live matrix",
        "part_box_m": [-0.059, 0.0, -0.013, 0.059, 0.008, 0.013],
        "bodies": [
            {
                "id": "B0",
                "edges": [
                    {
                        "id": "B0E60",
                        "curve_type": "circle",
                        "curve_parameters": [-0.025, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0035],
                    },
                    {
                        "id": "B0E62",
                        "curve_type": "circle",
                        "curve_parameters": [0.025, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0035],
                    },
                ],
                "faces": [
                    {
                        "id": "B0F0",
                        "loops": [],
                        "surface_parameters": {
                            "origin": [0.0, 0.004, 0.0],
                            "axis": [0.0, 0.0, 1.0],
                        },
                    },
                    {"id": "B0F18", "edge_ids": ["B0E60"]},
                    {"id": "B0F20", "edge_ids": ["B0E62"]},
                ],
            }
        ],
    }


def _snapshot(root: dict[str, Any], phase: str, case_id: str) -> Any:
    verification = root.get(phase) or {}
    views = verification.get("views") or []
    if case_id == "basic_projected":
        return views
    key = (
        "section"
        if case_id.endswith("section") and case_id != "broken_out_section"
        else "c2"
        if case_id == "broken_out_section" or case_id.startswith("detail_view")
        else "auxiliary"
        if case_id.startswith("auxiliary_")
        else "center_elements"
    )
    for row in views:
        if key in row:
            return row[key]
    raise RuntimeError(f"{case_id} {phase} fingerprint is missing")


def _rewrite_evidence(value: Any, geometry: Path) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "report_path":
                value[key] = str(geometry)
            else:
                _rewrite_evidence(item, geometry)
    elif isinstance(value, list):
        for item in value:
            _rewrite_evidence(item, geometry)


def _box(x_min: float, y_min: float, x_max: float, y_max: float) -> dict[str, float]:
    return {
        "x_min_m": x_min,
        "y_min_m": y_min,
        "x_max_m": x_max,
        "y_max_m": y_max,
    }


def _require_completed(response: dict[str, Any], label: str) -> None:
    if response.get("status") != "COMPLETED" or not response.get("verified"):
        error = response.get("error") or {}
        raise RuntimeError(
            f"{label} failed: {error.get('code')} {error.get('message')}"
        )


def _single(directory: Path, suffix: str) -> Path:
    matches = sorted(
        (path for path in directory.iterdir() if path.suffix.lower() == suffix),
        key=lambda path: path.name.lower(),
    )
    if len(matches) != 1:
        raise ValueError(f"validation_dir must contain exactly one {suffix} file")
    return matches[0]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
