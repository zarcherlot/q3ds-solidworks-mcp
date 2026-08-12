import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
os.sys.path.insert(0, str(_ROOT))

from drawing_planner.model_gateway import (  # noqa: E402
    CallablePlanningModelGateway,
    PlanningModelResponseError,
)
from drawing_planner.planning_models import PlanningRequest, PlanningResult  # noqa: E402
from drawing_planner.planning_prompt_compiler import (  # noqa: E402
    PlannerProfileUnavailable,
    RepositoryPlanningPromptCompiler,
)


class PlannerOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "part.SLDPRT"
        self.drawing = self.root / "blank.SLDDRW"
        self.readiness = self.root / "drawing-readiness.json"
        self.geometry = self.root / "model-geometry.json"
        self.model.write_bytes(b"model")
        self.drawing.write_bytes(b"blank")
        self.readiness.write_text('{"status":"ready"}', encoding="utf-8")
        self.geometry.write_text('{"status":"success"}', encoding="utf-8")
        images = []
        for view in ("front", "back", "left", "right", "top", "bottom"):
            path = self.root / f"{view}.png"
            path.write_bytes(("png-" + view).encode("ascii"))
            images.append({"view": view, "path": str(path), "sha256": _sha(path)})
        self.manifest = self.root / "drawing-planning-handoff.json"
        payload = {
            "protocol_id": "q3ds-drawing-planning-handoff",
            "schema_version": "1.0",
            "handoff_id": "DH-orchestration-1",
            "status": "ready",
            "model": {
                "path": str(self.model),
                "sha256": _sha(self.model),
                "configuration": "Default",
                "display_state": "Display State-1",
            },
            "blank_drawing": {
                "path": str(self.drawing),
                "sha256": _sha(self.drawing),
                "blank": True,
            },
            "readiness_report": {
                "path": str(self.readiness),
                "sha256": _sha(self.readiness),
            },
            "geometry_report": {
                "path": str(self.geometry),
                "sha256": _sha(self.geometry),
            },
            "standard_view_images": images,
            "drawing_context": {
                "sheet": {
                    "name": "Sheet1",
                    "format_name": "A3-Landscape",
                    "width_m": 0.42,
                    "height_m": 0.297,
                },
                "projection_method": "first_angle",
                "sheet_scale": {"numerator": 1, "denominator": 1},
                "inner_frame": {
                    "bounds_sheet_m": {
                        "x_min_m": 0.01,
                        "y_min_m": 0.01,
                        "x_max_m": 0.41,
                        "y_max_m": 0.287,
                    },
                    "safe_zone_sheet_m": {
                        "x_min_m": 0.02,
                        "y_min_m": 0.02,
                        "x_max_m": 0.4,
                        "y_max_m": 0.277,
                    },
                },
                "reserved_zones": [],
            },
            "blocking_issues": [],
            "open_questions": [],
        }
        self.manifest.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        self.request = PlanningRequest(
            handoff_manifest_path=str(self.manifest),
            handoff_manifest_sha256=_sha(self.manifest),
            planner_profile="production",
            publication_directory=str(self.root),
            user_requirements={"preferred_projection": "first_angle"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_repository_compiler_binds_complete_handoff_and_profile(self):
        prompt = RepositoryPlanningPromptCompiler().compile(self.request)
        self.assertEqual(prompt.planner_profile, "production")
        self.assertEqual(prompt.input_manifest_sha256, _sha(self.manifest))
        self.assertEqual(len(prompt.artifacts), 9)
        self.assertEqual(
            {artifact.view for artifact in prompt.artifacts if artifact.view},
            {"front", "back", "left", "right", "top", "bottom"},
        )
        self.assertEqual(
            prompt.schema_sha256,
            "ebe92b04bd1b4a4f0fd7ff6a6314e36f531e06421b0ae8f803fbb86ab209ceac",
        )
        rendered = "\n".join(message["content"] for message in prompt.messages)
        self.assertIn("DH-orchestration-1", rendered)
        self.assertIn(self.request.handoff_manifest_sha256, rendered)

    def test_debug_compiler_routes_reference_map_before_final_prompt(self):
        prompt_root = self.root / "prompt-lab"
        (prompt_root / "references" / "general").mkdir(parents=True)
        (prompt_root / "references" / "categories").mkdir(parents=True)
        (prompt_root / "references" / "features").mkdir(parents=True)
        (prompt_root / "skill.md").write_text("Skill root", encoding="utf-8")
        files = {
            "references/general/base.md": "Base rules",
            "references/general/deferred.md": "Deferred rules",
            "references/categories/shaft.md": "Shaft rules",
            "references/categories/plate.md": "Plate rules",
            "references/features/overall.md": "Overall rules",
            "references/features/holes.md": "Hole rules",
        }
        for relative, content in files.items():
            (prompt_root / relative).write_text(content, encoding="utf-8")
        (prompt_root / "references" / "categories" / "shaft.jpg").write_bytes(
            b"\xff\xd8\xff\xe0jpeg-reference"
        )
        (prompt_root / "references" / "reference-map.md").write_text(
            """# Map
## 基础资料
- [Base](general/base.md)
## 第二步：零件类别与视图资料
- [Shaft](categories/shaft.md) [Shaft image](categories/shaft.jpg)
- [Plate](categories/plate.md)
## 第三步：特征标注资料
- [Overall](features/overall.md)
- [Holes](features/holes.md)
## 默认不启用
- [Deferred](general/deferred.md)
""",
            encoding="utf-8",
        )
        request = PlanningRequest(
            handoff_manifest_path=str(self.manifest),
            handoff_manifest_sha256=_sha(self.manifest),
            planner_profile="debug",
            debug_prompt_directory=str(prompt_root),
            publication_directory=str(self.root),
        )
        compiler = RepositoryPlanningPromptCompiler()

        router = compiler.compile_reference_selection(request)
        self.assertIsNotNone(router)
        assert router is not None
        self.assertEqual(router.purpose, "debug_reference_selection")
        self.assertEqual(len(router.artifacts), 9)
        self.assertEqual(
            router.response_schema["properties"]["category_references"]["items"][
                "enum"
            ],
            [
                "references/categories/shaft.md",
                "references/categories/plate.md",
            ],
        )

        final = compiler.compile(
            request,
            debug_reference_selection={
                "category_references": ["references/categories/shaft.md"],
                "feature_references": ["references/features/overall.md"],
                "deferred_references": [],
            },
        )
        rendered = "\n".join(message["content"] for message in final.messages)
        self.assertIn("Shaft rules", rendered)
        self.assertIn("Overall rules", rendered)
        self.assertNotIn("Plate rules", rendered)
        self.assertNotIn("Hole rules", rendered)
        self.assertNotIn("Deferred rules", rendered)
        self.assertEqual(len(final.artifacts), 10)
        reference_image = final.artifacts[-1]
        self.assertEqual(reference_image.kind, "debug_reference_image")
        self.assertEqual(reference_image.media_type, "image/jpeg")
        self.assertTrue(reference_image.path.endswith("shaft.jpg"))

    def test_public_planning_contracts_match_strict_domain_models(self):
        contracts = _ROOT / "drawing_planner" / "contracts"
        for model, name in (
            (PlanningRequest, "planning-request.schema.json"),
            (PlanningResult, "planning-result.schema.json"),
        ):
            committed = json.loads((contracts / name).read_text(encoding="utf-8"))
            self.assertEqual(
                committed.pop("$schema"),
                "https://json-schema.org/draft/2020-12/schema",
            )
            self.assertEqual(committed, model.model_json_schema())

    def test_repository_compiler_rechecks_artifacts_before_model_use(self):
        self.geometry.write_text('{"status":"changed"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "VP-INTEGRITY-ARTIFACT-HASH"):
            RepositoryPlanningPromptCompiler().compile(self.request)

    def test_repository_compiler_rejects_unknown_profile(self):
        request = PlanningRequest(
            handoff_manifest_path=str(self.manifest),
            handoff_manifest_sha256=_sha(self.manifest),
            planner_profile="untrusted",
            publication_directory=str(self.root),
        )
        with self.assertRaisesRegex(
            PlannerProfileUnavailable, "unknown planner_profile"
        ):
            RepositoryPlanningPromptCompiler().compile(request)

    def test_callable_gateway_pins_identity_and_rejects_extra_output(self):
        prompt = RepositoryPlanningPromptCompiler().compile(self.request)

        async def valid_runner(_prompt):
            return {
                "response_id": "response-1",
                "plan": {
                    "protocol_id": "solidworks-view-plan",
                    "schema_version": "1.4",
                },
            }

        gateway = CallablePlanningModelGateway(
            provider="test-provider",
            model="test-model",
            runner=valid_runner,
        )
        response = asyncio.run(gateway.generate(prompt))
        self.assertEqual(response.provider, "test-provider")
        self.assertEqual(response.model, "test-model")
        self.assertEqual(response.response_id, "response-1")

        async def invalid_runner(_prompt):
            return {"response_id": None, "plan": {}, "unexpected": True}

        invalid_gateway = CallablePlanningModelGateway(
            provider="test-provider",
            model="test-model",
            runner=invalid_runner,
        )
        with self.assertRaises(PlanningModelResponseError):
            asyncio.run(invalid_gateway.generate(prompt))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
