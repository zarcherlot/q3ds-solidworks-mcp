import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from drawing_planner.feature_taxonomy import (
    MechanicalFeatureTaxonomy,
    experimental_mechanical_taxonomy,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "drawing_planner" / "contracts" / "feature-taxonomy.schema.json"
TAXONOMY_PATH = (
    ROOT
    / "drawing_planner"
    / "taxonomies"
    / "mechanical-features-1.0.0-experimental.json"
)


def _payload() -> dict:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def test_experimental_taxonomy_matches_json_schema_and_strict_model():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = _payload()

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(payload)
    taxonomy = MechanicalFeatureTaxonomy.model_validate(payload)

    assert taxonomy.status == "experimental"
    assert taxonomy.feature_class("geometry.slot.obround").parent_code == "geometry.slot"
    assert taxonomy.feature_class("context.cast").kind == "manufacturing_context"
    with pytest.raises(KeyError, match="unknown mechanical feature class"):
        taxonomy.feature_class("hole.whatever")


def test_pinned_experimental_loader_has_no_runtime_discovery():
    taxonomy = experimental_mechanical_taxonomy()

    assert taxonomy.taxonomy_version == "1.0.0-experimental"
    assert len(taxonomy.feature_classes) >= 25


def test_taxonomy_rejects_duplicate_codes():
    payload = _payload()
    payload["feature_classes"].append(dict(payload["feature_classes"][0]))

    with pytest.raises(ValidationError, match="feature class codes must be unique"):
        MechanicalFeatureTaxonomy.model_validate(payload)


def test_taxonomy_rejects_unknown_parent():
    payload = _payload()
    payload["feature_classes"][5]["parent_code"] = "geometry.missing"

    with pytest.raises(ValidationError, match="references unknown parent"):
        MechanicalFeatureTaxonomy.model_validate(payload)


def test_taxonomy_rejects_parent_cycle():
    payload = _payload()
    payload["feature_classes"][0]["parent_code"] = "overall.child"
    payload["feature_classes"].append(
        {
            **payload["feature_classes"][0],
            "code": "overall.child",
            "parent_code": "overall",
        }
    )

    with pytest.raises(ValidationError, match="hierarchy contains a cycle"):
        MechanicalFeatureTaxonomy.model_validate(payload)


def test_taxonomy_rejects_incomplete_requirement_namespace():
    payload = _payload()
    payload["requirement_kinds"].remove("transition_detail")

    with pytest.raises(ValidationError, match="complete namespace"):
        MechanicalFeatureTaxonomy.model_validate(payload)


def test_taxonomy_rejects_kind_change_below_parent():
    payload = _payload()
    payload["feature_classes"][5]["kind"] = "specialized_structure"

    with pytest.raises(ValidationError, match="changes kind below parent"):
        MechanicalFeatureTaxonomy.model_validate(payload)


def test_taxonomy_rejects_duplicate_classification_axis_root():
    payload = _payload()
    payload["feature_classes"].append(
        {
            **payload["feature_classes"][0],
            "code": "overall.alternate_root",
        }
    )

    with pytest.raises(ValidationError, match="exactly the five classification-axis roots"):
        MechanicalFeatureTaxonomy.model_validate(payload)
