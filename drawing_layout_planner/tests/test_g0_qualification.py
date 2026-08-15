from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from drawing_layout_planner.g0_evidence import G0_CAPABILITY_IDS, load_g0_capability_manifest
from drawing_layout_planner.g0_qualification import SCHEMA_PATH


def test_qualification_schema_is_valid_and_uses_the_frozen_catalog():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert tuple(schema["$defs"]["capabilityId"]["enum"]) == G0_CAPABILITY_IDS


def test_promoted_registry_is_complete_and_bound_to_live_qualification():
    manifest = load_g0_capability_manifest()
    statuses = {row["id"]: row["status"] for row in manifest["capabilities"]}
    assert set(statuses) == set(G0_CAPABILITY_IDS)
    assert "planned" not in statuses.values()
    assert statuses["dimension_display_bounds"] == "supported"
    assert statuses["section_symbol_bounds"] == "supported"
    assert manifest["registry_version"] == "1.1.0"
    assert manifest["live_evidence"]["qualification_id"] == "G0-EXACT-COMPLETE-20260815"
