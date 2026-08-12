import json
import os
import sys
import tempfile
import unittest

from pydantic import ValidationError


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADAPTER_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_ADAPTER_DIR))
sys.path.insert(0, _ADAPTER_DIR)

from semantic_models import parse_drawing_plan  # noqa: E402


def _valid_plan(folder):
    model = os.path.join(folder, "source.SLDPRT")
    template = os.path.join(folder, "template.DRWDOT")
    return {
        "schema_version": "1.0",
        "model": {"path": model},
        "drawing": {
            "template_path": template,
            "output_path": os.path.join(folder, "semantic-test-output.SLDDRW"),
        },
        "sheet": {"scale_numerator": 1.0, "scale_denominator": 1.0},
        "views": [
            {
                "id": "front",
                "kind": "base",
                "orientation": "front",
                "position": {"x": 0.12, "y": 0.12},
            },
            {
                "id": "top",
                "kind": "projected",
                "parent_id": "front",
                "position": {"x": 0.12, "y": 0.23},
            },
        ],
    }


class DrawingPlanModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        for name in ("source.SLDPRT", "template.DRWDOT"):
            with open(os.path.join(self.temp.name, name), "wb") as handle:
                handle.write(b"contract-test-placeholder")

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_and_normalizes_complete_plan(self):
        plan = parse_drawing_plan(json.dumps(_valid_plan(self.temp.name), ensure_ascii=False))
        normalized = plan.execution_dict()
        self.assertEqual(normalized["views"][0]["scale_mode"], "sheet")
        self.assertEqual(normalized["views"][1]["scale_mode"], "parent")
        self.assertEqual(len(plan.sha256()), 64)

    def test_rejects_unknown_fields(self):
        raw = _valid_plan(self.temp.name)
        raw["views"][0]["nearest_edge"] = True
        with self.assertRaises(ValidationError):
            parse_drawing_plan(json.dumps(raw, ensure_ascii=False))

    def test_rejects_forward_or_diagonal_projected_view(self):
        raw = _valid_plan(self.temp.name)
        raw["views"][1]["parent_id"] = "missing"
        with self.assertRaises(ValidationError):
            parse_drawing_plan(json.dumps(raw, ensure_ascii=False))

        raw = _valid_plan(self.temp.name)
        raw["views"][1]["position"] = {"x": 0.18, "y": 0.23}
        with self.assertRaises(ValidationError):
            parse_drawing_plan(json.dumps(raw, ensure_ascii=False))

    def test_rejects_overwriting_an_input(self):
        raw = _valid_plan(self.temp.name)
        raw["drawing"]["output_path"] = raw["model"]["path"]
        with self.assertRaises(ValidationError):
            parse_drawing_plan(json.dumps(raw, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
