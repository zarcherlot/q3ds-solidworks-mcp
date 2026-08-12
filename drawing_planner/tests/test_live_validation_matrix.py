import importlib.util
import json
from pathlib import Path

from drawing_planner.validators.schema import ViewPlanSchemaValidator


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "run_view_plan_live_matrix.py"
_SPEC = importlib.util.spec_from_file_location("run_view_plan_live_matrix", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
live = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(live)


def test_all_live_case_plans_satisfy_viewplan_schema(tmp_path):
    fixture = json.loads(
        (_ROOT / "drawing_planner/tests/fixtures/view_plan.valid.json").read_text(
            encoding="utf-8"
        )
    )
    model = tmp_path / "part.SLDPRT"
    drawing = tmp_path / "ready-blank.SLDDRW"
    geometry = tmp_path / "model-geometry.json"
    readiness = tmp_path / "drawing-readiness.json"
    model.write_bytes(b"model")
    drawing.write_bytes(b"drawing")
    geometry.write_text("{}", encoding="utf-8")
    readiness.write_text("{}", encoding="utf-8")
    images = {}
    for name in live._VIEWS:
        path = tmp_path / f"{name}.png"
        path.write_bytes(live._PNG)
        images[name] = path

    validator = ViewPlanSchemaValidator()
    for case_id in live._CASE_IDS:
        plan = live._base_plan(
            fixture,
            model,
            drawing,
            geometry,
            readiness,
            images,
            "Default",
            case_id,
        )
        live._configure_case(plan, case_id, geometry)
        issues = validator.validate(plan)
        assert not issues, (case_id, issues)


def test_live_case_inventory_covers_every_supported_family():
    cases = set(live._CASE_IDS)
    assert {
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
    } == cases
