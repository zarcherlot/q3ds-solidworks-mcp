from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "drawing_layout_planner"
PROMPT_PACK = PACKAGE / "prompt_packs" / "native-v1"
SKILL = ROOT / ".codex" / "skills" / "solidworks-finalize-drawing-layout" / "SKILL.md"
SERVER = ROOT / "adapters" / "claude" / "server.py"


def test_g6_prompt_pack_is_immutable_and_request_scoped() -> None:
    manifest = json.loads((PROMPT_PACK / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        (PROMPT_PACK / "system.md").read_bytes()
        + b"\n---\n"
        + (PROMPT_PACK / "task.md").read_bytes()
    ).hexdigest()
    assert manifest["producer"]["ruleset_sha256"] == digest
    assert manifest["protocol_id"] == "solidworks-drawing-layout-planning-request"
    assert manifest["files"] == ["system.md", "task.md"]
    text = (PROMPT_PACK / "system.md").read_text(encoding="utf-8")
    assert "exactly one complete LayoutPlanningRequest" in text
    assert "deterministic solver owns final coordinates" in text


def test_g6_skill_cannot_bypass_dimension_request_or_private_boundary() -> None:
    text = SKILL.read_text(encoding="utf-8")
    allowed = [
        "solidworks_status",
        "initialize_part_drawing_layout_handoff",
        "publish_validated_part_drawing_layout_plan",
        "validate_part_drawing_layout_plan",
        "create_final_part_drawing",
        "verify_final_part_drawing",
    ]
    assert "complete source DimensionPlanningRequest embedded unchanged" in text
    assert "Do not accept a ViewPlan drawing" in text
    for tool in allowed:
        assert f"- `{tool}`" in text
    for private in (
        "validate_frozen_part_drawing_layout_plan",
        "execute_part_drawing_layout_plan",
        "verify_committed_part_drawing_layout_plan",
        "qualify_final_part_drawing",
        "verify_qualified_final_part_drawing",
    ):
        assert f"- `{private}`" not in text


def test_g6_public_tools_wrap_all_three_private_layout_routes() -> None:
    source = SERVER.read_text(encoding="utf-8")
    for public in (
        "publish_validated_part_drawing_layout_plan",
        "validate_part_drawing_layout_plan",
        "create_final_part_drawing",
        "verify_final_part_drawing",
    ):
        assert f"def {public}(" in source
    assert '"validate_frozen_part_drawing_layout_plan"' in source
    assert '"execute_part_drawing_layout_plan"' in source
    assert '"verify_committed_part_drawing_layout_plan"' in source
    assert "_require_executable_layout_plan(validation, assessment)" in source
