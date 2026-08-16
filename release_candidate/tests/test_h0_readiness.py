from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from release_candidate.h0_readiness import audit_h0_readiness


ROOT = Path(__file__).resolve().parents[2]


def test_current_repository_h0_gate_fails_closed_on_unpromoted_f7() -> None:
    report = audit_h0_readiness(ROOT)
    assert report["status"] == "blocked"
    codes = {row["code"] for row in report["blockers"]}
    assert "f7-production-capabilities-not-promoted" in codes
    blocker = next(
        row
        for row in report["blockers"]
        if row["code"] == "f7-production-capabilities-not-promoted"
    )
    assert any(value == "dimension_type.linear" for value in blocker["references"])
    assert any(
        value == "element.save_reopen_stable_identity"
        for value in blocker["references"]
    )


def test_h0_report_matches_strict_contract() -> None:
    report = audit_h0_readiness(ROOT)
    schema = json.loads(
        (ROOT / "release_candidate/contracts/h0-readiness.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(report)
    assert report["semantic_mcp"]["tool_count"] == 24
    assert report["semantic_mcp"]["prompt_count"] == 0
    assert [row["name"] for row in report["skills"]] == [
        "bootstrap-solidworks-host",
        "solidworks-initialize-drawing-handoff",
        "solidworks-create-drawing-views",
        "solidworks-dimension-drawing",
        "solidworks-finalize-drawing-layout",
    ]


def test_blocked_report_contract_can_record_surface_drift() -> None:
    report = audit_h0_readiness(ROOT)
    drifted = copy.deepcopy(report)
    drifted["semantic_mcp"]["tools"].pop()
    drifted["semantic_mcp"]["tool_count"] = 23
    drifted["blockers"].append(
        {
            "code": "live-semantic-surface-drift",
            "message": "test drift",
            "references": ["semantic-tools.schema.json"],
        }
    )
    schema = json.loads(
        (ROOT / "release_candidate/contracts/h0-readiness.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(drifted)


def test_h0_script_publishes_blocked_report_once(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "check_h0_release_readiness",
        ROOT / "scripts/check_h0_release_readiness.py",
    )
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    output = tmp_path / "h0-readiness.json"
    script._publish_once({"status": "blocked"}, output)
    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "blocked"}
    with pytest.raises(FileExistsError):
        script._publish_once({"status": "ready"}, output)
