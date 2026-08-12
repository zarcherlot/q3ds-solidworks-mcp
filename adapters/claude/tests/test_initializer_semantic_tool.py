import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADAPTER_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _ADAPTER_DIR)

import server  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executor_handoff(model: Path, publication: Path) -> dict:
    blank = publication / "initializer-blank.SLDDRW"
    readiness = publication / "drawing-readiness.json"
    geometry = publication / "model-geometry.json"
    blank.write_bytes(b"blank")
    readiness.write_text('{"status":"ready"}', encoding="utf-8")
    geometry.write_text(
        json.dumps(
            {
                "status": "success",
                "part_box_m": {
                    "x_min_m": 0,
                    "y_min_m": 0,
                    "z_min_m": 0,
                    "x_max_m": 0.1,
                    "y_max_m": 0.1,
                    "z_max_m": 0.01,
                },
                "bodies": [{"id": "B0", "faces": [], "edges": []}],
            }
        ),
        encoding="utf-8",
    )
    images = []
    for view in ("front", "back", "left", "right", "top", "bottom"):
        path = publication / f"{view}.png"
        path.write_bytes(view.encode("ascii"))
        images.append({"view": view, "path": str(path), "sha256": _sha(path)})
    manifest = publication / "drawing-planning-handoff.json"
    manifest.write_text(
        json.dumps(
            {
                "protocol_id": "q3ds-drawing-planning-handoff",
                "schema_version": "1.0",
                "handoff_id": "DH-unit-test",
                "status": "ready",
                "model": {
                    "path": str(model),
                    "sha256": _sha(model),
                    "configuration": "Default",
                    "display_state": "Display State-1",
                },
                "blank_drawing": {
                    "path": str(blank),
                    "sha256": _sha(blank),
                    "blank": True,
                },
                "readiness_report": {
                    "path": str(readiness),
                    "sha256": _sha(readiness),
                },
                "geometry_report": {
                    "path": str(geometry),
                    "sha256": _sha(geometry),
                },
                "standard_view_images": images,
                "drawing_context": {
                    "sheet": {
                        "name": "Sheet1",
                        "format_name": "A3",
                        "width_m": 0.42,
                        "height_m": 0.297,
                    },
                    "projection_method": "first_angle",
                    "sheet_scale": {"numerator": 1, "denominator": 1},
                    "inner_frame": {
                        "bounds_sheet_m": {
                            "x_min_m": 0.01,
                            "y_min_m": 0.01,
                            "x_max_m": 0.41,
                            "y_max_m": 0.287,
                        },
                        "safe_zone_sheet_m": {
                            "x_min_m": 0.02,
                            "y_min_m": 0.02,
                            "x_max_m": 0.40,
                            "y_max_m": 0.277,
                        },
                    },
                    "reserved_zones": [],
                },
                "blocking_issues": [],
                "open_questions": [],
            }
        ),
        encoding="utf-8",
    )
    return {
        "status": "COMPLETED",
        "verified": True,
        "stateVersion": 1,
        "result_geometry": {
            "kind": "drawing_planning_handoff",
            "manifest_path": str(manifest),
            "manifest_sha256": _sha(manifest),
            "publication_directory": str(publication),
            "verified": True,
        },
    }


def test_initializer_routes_one_semantic_transaction_and_revalidates_handoff(tmp_path):
    model = tmp_path / "part.SLDPRT"
    template = tmp_path / "A3.DRWDOT"
    publication = tmp_path / "publication"
    model.write_bytes(b"model")
    template.write_bytes(b"template")
    publication.mkdir()

    calls = []

    def fake_execute(tool, params, *, mutating):
        calls.append((tool, params, mutating))
        return _write_executor_handoff(model, publication)

    with patch.object(server, "_execute", side_effect=fake_execute):
        payload = json.loads(
            server.initialize_part_drawing_handoff(
                str(model), str(template), str(publication)
            )
        )

    assert payload["ok"] is True
    assert payload["handoff_integrity"] == "pass"
    assert payload["planning_request"]["planner_profile"] == "production"
    assert payload["planning_request_sha256"] == server._planning_request_sha256(
        server.PlanningRequest(**payload["planning_request"])
    )
    assert calls == [
        (
            "initialize_part_drawing_handoff",
            {
                "model_path": str(model.resolve()),
                "drawing_template_path": str(template.resolve()),
                "publication_directory": str(publication.resolve()),
                "image_width": 1024,
                "image_height": 768,
            },
            True,
        )
    ]


def test_initializer_refuses_any_existing_output_before_executor(tmp_path):
    model = tmp_path / "part.SLDPRT"
    template = tmp_path / "A3.DRWDOT"
    publication = tmp_path / "publication"
    model.write_bytes(b"model")
    template.write_bytes(b"template")
    publication.mkdir()
    (publication / "front.png").write_bytes(b"existing")

    with patch.object(server, "_execute") as execute:
        with pytest.raises(ValueError, match="initializer outputs must all be new"):
            server.initialize_part_drawing_handoff(
                str(model), str(template), str(publication)
            )
    execute.assert_not_called()
