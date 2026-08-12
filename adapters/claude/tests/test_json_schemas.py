import json
import hashlib
import os

import jsonschema


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))


def test_checked_in_json_schemas_are_valid_draft_2020_12():
    paths = [
        os.path.join(
            _ROOT, "solidworks-execution", "contracts", "drawing-plan.schema.json"
        ),
        os.path.join(
            _ROOT,
            "adapters",
            "claude",
            "contracts",
            "semantic-tools.schema.json",
        ),
        os.path.join(
            _ROOT,
            "adapters",
            "claude",
            "contracts",
            "drawing-plan-compat-tools.schema.json",
        ),
        os.path.join(
            _ROOT, "drawing_planner", "contracts", "prompt-pack.schema.json"
        ),
        os.path.join(
            _ROOT, "drawing_planner", "contracts", "prompt-request.schema.json"
        ),
        os.path.join(
            _ROOT,
            "drawing_planner",
            "contracts",
            "executor-capabilities.schema.json",
        ),
        os.path.join(
            _ROOT,
            "drawing_planner",
            "contracts",
            "feature-taxonomy.schema.json",
        ),
        os.path.join(
            _ROOT,
            "drawing_planner",
            "contracts",
            "model-semantic-features.schema.json",
        ),
        os.path.join(
            _ROOT, "drawing_planner", "contracts", "view-plan.schema.json"
        ),
    ]
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            schema = json.load(handle)
        jsonschema.Draft202012Validator.check_schema(schema)


def test_current_executor_capability_manifest_matches_its_schema():
    schema_path = os.path.join(
        _ROOT,
        "drawing_planner",
        "contracts",
        "executor-capabilities.schema.json",
    )
    manifest_path = os.path.join(
        _ROOT, "drawing_planner", "capabilities", "current.json"
    )
    with open(schema_path, encoding="utf-8") as handle:
        schema = json.load(handle)
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    jsonschema.Draft202012Validator(schema).validate(manifest)


def test_repository_view_plan_schema_matches_contract_lock():
    contract_root = os.path.join(_ROOT, "drawing_planner", "contracts")
    lock_path = os.path.join(contract_root, "view-plan.contract.json")
    schema_path = os.path.join(contract_root, "view-plan.schema.json")
    with open(lock_path, encoding="utf-8") as handle:
        contract_lock = json.load(handle)
    with open(schema_path, "rb") as handle:
        actual_sha256 = hashlib.sha256(handle.read()).hexdigest()
    assert contract_lock["runtime_authority"] == "repository"
    assert contract_lock["contract"] == "solidworks-view-plan"
    assert contract_lock["contract_version"] == "1.4"
    assert actual_sha256 == contract_lock["sha256"]
