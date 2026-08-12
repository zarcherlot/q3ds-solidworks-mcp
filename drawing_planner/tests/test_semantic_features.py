import hashlib
import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from drawing_planner.feature_taxonomy import experimental_mechanical_taxonomy
from drawing_planner.semantic_features import (
    ModelSemanticFeatures,
    assess_closed_set_coverage,
    load_model_semantic_features,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "drawing_planner" / "contracts" / "model-semantic-features.schema.json"
TAXONOMY_PATH = ROOT / "drawing_planner" / "taxonomies" / "mechanical-features-1.0.0-experimental.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _geometry() -> dict:
    return {
        "schema_version": 1,
        "status": "success",
        "bodies": [
            {
                "id": "B0",
                "faces": [
                    {"id": "B0F0", "edge_ids": ["B0E0"]},
                    {"id": "B0F1", "edge_ids": ["B0E1"]},
                    {"id": "B0F2", "edge_ids": ["B0E2"]},
                ],
                "edges": [
                    {"id": "B0E0"},
                    {"id": "B0E1"},
                    {"id": "B0E2"},
                ],
                "vertices": [],
            }
        ],
    }


def _artifact(root: Path) -> tuple[dict, Path, Path, Path]:
    model = root / "part.SLDPRT"
    geometry = root / "model-geometry.json"
    taxonomy = root / TAXONOMY_PATH.name
    model.write_bytes(b"fixture-model")
    geometry.write_text(json.dumps(_geometry()), encoding="utf-8")
    taxonomy.write_bytes(TAXONOMY_PATH.read_bytes())
    artifact = {
        "protocol_id": "q3ds-solidworks-model-semantic-features",
        "schema_version": "1.0",
        "artifact_id": "MSF-FIXTURE-001",
        "status": "complete",
        "producer": {
            "name": "q3ds-test-fixture",
            "version": "1.0.0",
            "extraction_mode": "offline_fixture",
        },
        "model": {
            "path": str(model),
            "sha256": _sha(model),
            "configuration": "Default",
            "display_state": "Display State-1",
        },
        "geometry_report": {"path": str(geometry), "sha256": _sha(geometry)},
        "taxonomy": {
            "taxonomy_id": "mechanical-features",
            "taxonomy_version": "1.0.0-experimental",
            "path": str(taxonomy),
            "sha256": _sha(taxonomy),
        },
        "features": [
            {
                "feature_id": "FT-OVERALL-001",
                "feature_class": "overall.prismatic_or_plate",
                "parent_feature_id": None,
                "source_feature_ref": "Boss-Extrude1",
                "significance": ["manufacturing", "inspection"],
                "geometry_refs": {
                    "body_ids": ["B0"],
                    "face_ids": ["B0F0"],
                    "edge_ids": ["B0E0"],
                    "vertex_ids": [],
                },
                "axis": None,
                "normal": [0.0, 1.0, 0.0],
                "opening_count": None,
                "axial_extent": None,
                "occurrences": [],
                "evidence_status": "complete",
            },
            {
                "feature_id": "FT-HOLE-001",
                "feature_class": "geometry.hole.through",
                "parent_feature_id": None,
                "source_feature_ref": "Hole1",
                "significance": ["manufacturing", "assembly", "inspection"],
                "geometry_refs": {
                    "body_ids": ["B0"],
                    "face_ids": ["B0F1"],
                    "edge_ids": ["B0E1"],
                    "vertex_ids": [],
                },
                "axis": {
                    "origin_m": [0.0, 0.0, 0.0],
                    "direction": [0.0, 1.0, 0.0],
                },
                "normal": None,
                "opening_count": 1,
                "axial_extent": {
                    "start_m": 0.0,
                    "end_m": 0.01,
                    "effective_depth_m": 0.01,
                    "total_depth_m": 0.01,
                    "bottom_form": "through",
                },
                "occurrences": [
                    {
                        "occurrence_id": "OCC-HOLE-001",
                        "suppressed": False,
                        "geometry_refs": {
                            "body_ids": ["B0"],
                            "face_ids": ["B0F1"],
                            "edge_ids": ["B0E1"],
                            "vertex_ids": [],
                        },
                    },
                    {
                        "occurrence_id": "OCC-HOLE-002",
                        "suppressed": True,
                        "geometry_refs": {
                            "body_ids": ["B0"],
                            "face_ids": ["B0F2"],
                            "edge_ids": ["B0E2"],
                            "vertex_ids": [],
                        },
                    },
                ],
                "evidence_status": "complete",
            },
        ],
        "relations": [
            {
                "relation_id": "REL-PATTERN-001",
                "relation_class": "relation.pattern",
                "member_feature_ids": ["FT-HOLE-001"],
                "axis": {
                    "origin_m": [0.0, 0.0, 0.0],
                    "direction": [0.0, 1.0, 0.0],
                },
                "plane_normal": None,
                "evidence_status": "complete",
            }
        ],
        "required_feature_ids": ["FT-OVERALL-001", "FT-HOLE-001"],
        "exemptions": [],
        "open_questions": [],
    }
    return artifact, model, geometry, taxonomy


def test_semantic_feature_schema_and_realistic_fixture(tmp_path):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    artifact, *_ = _artifact(tmp_path)
    jsonschema.Draft202012Validator(schema).validate(artifact)
    parsed = ModelSemanticFeatures.model_validate(artifact)
    parsed.validate_bindings(experimental_mechanical_taxonomy(), _geometry())
    assert parsed.features[1].feature_id != "B0F1"
    assert parsed.features[1].occurrences[1].suppressed is True


def test_loader_verifies_model_geometry_and_taxonomy_hashes(tmp_path):
    artifact, *_ = _artifact(tmp_path)
    path = tmp_path / "model-semantic-features.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    loaded = load_model_semantic_features(
        path, taxonomy=experimental_mechanical_taxonomy()
    )
    assert loaded.artifact_id == "MSF-FIXTURE-001"

    artifact["model"]["sha256"] = "0" * 64
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="model SHA-256 mismatch"):
        load_model_semantic_features(path, taxonomy=experimental_mechanical_taxonomy())


def test_closed_set_coverage_rejects_omission_invention_duplicate_and_exemption_conflict(tmp_path):
    artifact, *_ = _artifact(tmp_path)
    parsed = ModelSemanticFeatures.model_validate(artifact)

    result = assess_closed_set_coverage(parsed, ["FT-OVERALL-001"])
    assert result.status == "fail"
    assert result.missing_feature_ids == ("FT-HOLE-001",)

    result = assess_closed_set_coverage(
        parsed,
        ["FT-OVERALL-001", "FT-HOLE-001", "FT-HOLE-001", "FT-NOT-REAL-001"],
    )
    assert result.duplicate_feature_ids == ("FT-HOLE-001",)
    assert result.unexpected_feature_ids == ("FT-NOT-REAL-001",)

    exempt = deepcopy(artifact)
    exempt["exemptions"] = [
        {
            "feature_id": "FT-HOLE-001",
            "reason_code": "not_drawing_significant",
            "reason": "fixture conflict",
            "controlled_source": "test requirement",
        }
    ]
    with pytest.raises(ValidationError, match="both required and exempt"):
        ModelSemanticFeatures.model_validate(exempt)


def test_semantic_artifact_rejects_brep_id_as_feature_id(tmp_path):
    artifact, *_ = _artifact(tmp_path)
    artifact["features"][0]["feature_id"] = "B0F0"
    with pytest.raises(ValidationError):
        ModelSemanticFeatures.model_validate(artifact)


def test_incomplete_artifact_requires_open_question(tmp_path):
    artifact, *_ = _artifact(tmp_path)
    artifact["status"] = "incomplete"
    with pytest.raises(ValidationError, match="requires open questions"):
        ModelSemanticFeatures.model_validate(artifact)
