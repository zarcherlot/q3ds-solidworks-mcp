import json
from pathlib import Path

import jsonschema

from drawing_planner.planner_profiles import PROFILE_PROMPT_PACKS


_ROOT = Path(__file__).resolve().parents[2]
_EXTERNAL_RUNTIME_MARKERS = (
    "solidworks-plan-drawing-views",
    "solidworks-view-plan-executor",
    "Invoke-ViewPlanCli.ps1",
    "validate_frozen_view_plan",
    "execute_frozen_view_plan",
    "SOLIDWORKS_VIEW_PLAN_EXECUTOR_SKILL_DIR",
)


def test_transitional_executor_bridge_is_removed():
    assert not (_ROOT / "drawing_planner" / "executor_bridge.py").exists()
    package_source = (_ROOT / "drawing_planner" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "executor_bridge" not in package_source
    assert "frozen_view_plan_handoff" not in package_source


def test_production_planner_uses_only_the_repository_native_prompt_pack():
    assert dict(PROFILE_PROMPT_PACKS) == {
        "production": "native-v4",
        "debug": "native-v4",
    }
    pack_root = _ROOT / "drawing_planner" / "prompt_packs" / "native-v4"
    manifest = json.loads((pack_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (_ROOT / "drawing_planner" / "contracts" / "prompt-pack.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft202012Validator(schema).validate(manifest)
    combined = "\n".join(
        (pack_root / name).read_text(encoding="utf-8")
        for name in ("manifest.json", "system.md", "task.md")
    )
    for marker in _EXTERNAL_RUNTIME_MARKERS:
        assert marker not in combined


def test_production_python_boundary_has_no_external_executor_runtime_marker():
    paths = (
        _ROOT / "adapters" / "claude" / "server.py",
        _ROOT / "drawing_planner" / "__init__.py",
        _ROOT / "drawing_planner" / "planner_profiles.py",
        _ROOT / "drawing_planner" / "planning_prompt_compiler.py",
        _ROOT / "drawing_planner" / "prompt_pipeline.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for marker in _EXTERNAL_RUNTIME_MARKERS:
        assert marker not in combined
