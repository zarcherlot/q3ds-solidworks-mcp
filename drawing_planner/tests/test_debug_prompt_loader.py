import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema


_ROOT = Path(__file__).resolve().parents[2]
os.sys.path.insert(0, str(_ROOT))

from drawing_planner.debug_prompt_loader import (  # noqa: E402
    load_debug_prompt_directory,
    load_debug_reference_catalog,
)
from drawing_planner.planning_models import PlanningRequest  # noqa: E402
from drawing_planner.prompt_pipeline import compile_drawing_prompt  # noqa: E402


class DebugPromptLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prompt_root = self.root / "planner-skill"
        for relative in ("references/general", "references/categories", "references/features"):
            (self.prompt_root / relative).mkdir(parents=True, exist_ok=True)
        (self.prompt_root / "skill.md").write_text(
            "Main planning guidance", encoding="utf-8"
        )
        self._write("references/general/base.md", "Base guidance")
        self._write("references/general/deferred.md", "Deferred guidance")
        self._write("references/categories/shaft.md", "Shaft guidance")
        self._write("references/categories/plate.md", "Plate guidance")
        (self.prompt_root / "references/categories/shaft.png").write_bytes(
            b"\x89PNG\r\n\x1a\nfixture"
        )
        self._write("references/features/overall.md", "Overall guidance")
        self._write("references/features/holes.md", "Hole guidance")
        self._write(
            "references/reference-map.md",
            """# Map

## 基础资料
- [Base](general/base.md)

## 第二步：零件类别与视图资料
| 类别 | Markdown 规则 |
| --- | --- |
| 轴类 | [Shaft](categories/shaft.md) [Image](categories/shaft.png) |
| 板类 | [Plate](categories/plate.md) |

## 第三步：特征标注资料
- [Overall](features/overall.md)
- [Holes](features/holes.md)

## 默认不启用
- [Deferred](general/deferred.md)
""",
        )
        self.selection = {
            "category_references": ["references/categories/shaft.md"],
            "feature_references": ["references/features/overall.md"],
            "deferred_references": [],
        }

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, relative: str, text: str) -> None:
        (self.prompt_root / relative).write_text(text, encoding="utf-8")

    def test_catalog_builds_schema_from_reference_map(self):
        catalog = load_debug_reference_catalog(str(self.prompt_root))
        self.assertEqual(
            catalog.required_references, ("references/general/base.md",)
        )
        schema = catalog.response_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        self.assertEqual(
            schema["properties"]["category_references"]["items"]["enum"],
            [
                "references/categories/shaft.md",
                "references/categories/plate.md",
            ],
        )
        self.assertEqual(len(catalog.sha256), 64)
        self.assertEqual(
            catalog.visual_references,
            (("references/categories/shaft.md", ("references/categories/shaft.png",)),),
        )
        self.assertEqual(
            schema["properties"]["deferred_references"]["maxItems"], 0
        )
        enabled = catalog.response_schema(allow_deferred=True)
        self.assertEqual(
            enabled["properties"]["deferred_references"]["items"]["enum"],
            ["references/general/deferred.md"],
        )

    def test_loads_only_required_and_selected_reference_markdown(self):
        source = load_debug_prompt_directory(str(self.prompt_root), self.selection)
        self.assertEqual(
            source.files,
            (
                "skill.md",
                "references/reference-map.md",
                "references/general/base.md",
                "references/categories/shaft.md",
                "references/features/overall.md",
            ),
        )
        self.assertIn("Shaft guidance", source.text)
        self.assertNotIn("Plate guidance", source.text)
        self.assertNotIn("Hole guidance", source.text)
        self.assertNotIn("Deferred guidance", source.text)
        self.assertIn("references/categories/shaft.png", source.text)
        self.assertEqual(len(source.images), 1)
        self.assertEqual(
            source.images[0].relative_path, "references/categories/shaft.png"
        )
        self.assertEqual(source.images[0].media_type, "image/png")
        self.assertEqual(len(source.sha256), 64)

    def test_rejects_unknown_or_duplicate_model_selection(self):
        unknown = dict(self.selection)
        unknown["feature_references"] = ["references/features/invented.md"]
        with self.assertRaisesRegex(ValueError, "outside reference-map.md"):
            load_debug_prompt_directory(str(self.prompt_root), unknown)
        duplicate = dict(self.selection)
        duplicate["category_references"] = [
            "references/categories/shaft.md",
            "references/categories/shaft.md",
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_debug_prompt_directory(str(self.prompt_root), duplicate)

    def test_rejects_selected_image_with_wrong_content_type(self):
        (self.prompt_root / "references/categories/shaft.png").write_bytes(
            b"not-a-png"
        )
        with self.assertRaisesRegex(ValueError, "does not match its media type"):
            load_debug_prompt_directory(str(self.prompt_root), self.selection)

    def test_requires_skill_and_reference_map(self):
        (self.prompt_root / "skill.md").unlink()
        with self.assertRaisesRegex(ValueError, "skill.md"):
            load_debug_reference_catalog(str(self.prompt_root))
        (self.prompt_root / "skill.md").write_text("restored", encoding="utf-8")
        (self.prompt_root / "references" / "reference-map.md").unlink()
        with self.assertRaisesRegex(ValueError, "reference-map.md"):
            load_debug_reference_catalog(str(self.prompt_root))

    def test_reference_map_rejects_directory_escape(self):
        outside = self.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        reference_map = self.prompt_root / "references" / "reference-map.md"
        reference_map.write_text(
            reference_map.read_text(encoding="utf-8")
            + "\n## 第三步：特征标注资料\n- [Escape](../../outside.md)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "escapes its directory"):
            load_debug_reference_catalog(str(self.prompt_root))

    def test_debug_prompt_selection_is_bound_into_envelope(self):
        readiness = self.root / "drawing-readiness.json"
        geometry = self.root / "model-geometry.json"
        plan = self.root / "view_plan.json"
        readiness.write_text("{}", encoding="utf-8")
        geometry.write_text("{}", encoding="utf-8")
        images = {}
        for view in ("front", "back", "left", "right", "top", "bottom"):
            image = self.root / f"{view}.png"
            image.write_bytes(b"png")
            images[view] = str(image)

        envelope = compile_drawing_prompt(
            prompt_pack="native-v4",
            readiness_report_path=str(readiness),
            geometry_report_path=str(geometry),
            standard_view_image_paths=images,
            view_plan_output_path=str(plan),
            debug_prompt_directory=str(self.prompt_root),
            debug_reference_selection=self.selection,
        )
        self.assertIn("Shaft guidance", envelope["rendered_prompt"])
        self.assertNotIn("Plate guidance", envelope["rendered_prompt"])
        self.assertEqual(envelope["debug_prompt"]["files"][0], "skill.md")
        self.assertEqual(
            envelope["debug_prompt"]["selection"]["category_references"],
            ["references/categories/shaft.md"],
        )
        self.assertEqual(
            envelope["debug_prompt"]["images"][0]["relative_path"],
            "references/categories/shaft.png",
        )
        self.assertEqual(
            envelope["debug_prompt"]["images"][0]["media_type"], "image/png"
        )
        deferred = dict(self.selection)
        deferred["deferred_references"] = ["references/general/deferred.md"]
        with self.assertRaisesRegex(ValueError, "enable_deferred_tolerancing_rules"):
            compile_drawing_prompt(
                prompt_pack="native-v4",
                readiness_report_path=str(readiness),
                geometry_report_path=str(geometry),
                standard_view_image_paths=images,
                view_plan_output_path=str(plan),
                debug_prompt_directory=str(self.prompt_root),
                debug_reference_selection=deferred,
            )
        enabled = compile_drawing_prompt(
            prompt_pack="native-v4",
            readiness_report_path=str(readiness),
            geometry_report_path=str(geometry),
            standard_view_image_paths=images,
            view_plan_output_path=str(plan),
            user_requirements={"enable_deferred_tolerancing_rules": True},
            debug_prompt_directory=str(self.prompt_root),
            debug_reference_selection=deferred,
        )
        self.assertIn("Deferred guidance", enabled["rendered_prompt"])

    def test_debug_directory_is_limited_to_debug_profile(self):
        shared = {
            "handoff_manifest_path": str(self.root / "drawing-planning-handoff.json"),
            "handoff_manifest_sha256": "0" * 64,
            "publication_directory": str(self.root),
        }
        request = PlanningRequest(
            **shared,
            planner_profile="debug",
            debug_prompt_directory=str(self.prompt_root),
        )
        self.assertEqual(request.debug_prompt_directory, str(self.prompt_root.resolve()))
        with self.assertRaisesRegex(ValueError, "only valid with planner_profile=debug"):
            PlanningRequest(**shared, debug_prompt_directory=str(self.prompt_root))

    def test_debug_directory_can_come_from_environment(self):
        shared = {
            "handoff_manifest_path": str(self.root / "drawing-planning-handoff.json"),
            "handoff_manifest_sha256": "0" * 64,
            "publication_directory": str(self.root),
            "planner_profile": "debug",
        }
        with patch.dict(
            os.environ,
            {"PLANNER_DEBUG_PROMPT_DIRECTORY": str(self.prompt_root)},
        ):
            request = PlanningRequest(**shared)
        self.assertEqual(request.debug_prompt_directory, str(self.prompt_root.resolve()))


if __name__ == "__main__":
    unittest.main()
