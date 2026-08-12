import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
os.sys.path.insert(0, str(_ROOT))

from drawing_planner.capability_registry import current_registry  # noqa: E402
from drawing_planner.plan_store import PlanStore  # noqa: E402
from drawing_planner.planner_engine import PlannerEngine  # noqa: E402
from drawing_planner.planning_models import (  # noqa: E402
    CompiledPlanningPrompt,
    ModelPlanningResponse,
    PlanningInputArtifact,
    PlanningRequest,
    PlanningValidation,
    ValidationIssue,
)
from drawing_planner.validators.integrity import IntegrityValidationResult  # noqa: E402


_SHA = "a" * 64


class _Compiler:
    def __init__(self):
        self.calls = 0

    def compile(self, request):
        self.calls += 1
        root = Path(request.handoff_manifest_path).parent
        artifacts = [
            PlanningInputArtifact(
                kind="handoff_manifest",
                path=request.handoff_manifest_path,
                sha256=request.handoff_manifest_sha256,
                media_type="application/json",
            ),
            PlanningInputArtifact(
                kind="readiness_report",
                path=str(root / "drawing-readiness.json"),
                sha256=_SHA,
                media_type="application/json",
            ),
            PlanningInputArtifact(
                kind="geometry_report",
                path=str(root / "model-geometry.json"),
                sha256=_SHA,
                media_type="application/json",
            ),
        ]
        for view in ("front", "back", "left", "right", "top", "bottom"):
            artifacts.append(
                PlanningInputArtifact(
                    kind="standard_view_image",
                    path=str(root / f"{view}.png"),
                    sha256=_SHA,
                    media_type="image/png",
                    view=view,
                )
            )
        return CompiledPlanningPrompt(
            planner_profile=request.planner_profile,
            messages=(
                {"role": "system", "content": "core"},
                {"role": "user", "content": "task"},
            ),
            response_schema={"type": "object"},
            artifacts=tuple(artifacts),
            core_policy_sha256=_SHA,
            prompt_pack_sha256=_SHA,
            schema_sha256=_SHA,
            input_manifest_sha256=request.handoff_manifest_sha256,
            envelope_sha256=_SHA,
        )


class _Gateway:
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        return ModelPlanningResponse(
            provider="test",
            model="deterministic-fake",
            response_id="response-1",
            plan=self.plan,
        )


class _RoutingCompiler(_Compiler):
    def __init__(self):
        super().__init__()
        self.route_calls = 0
        self.selection = None

    def compile_reference_selection(self, request):
        self.route_calls += 1
        prompt = super().compile(request)
        self.calls -= 1
        return prompt.model_copy(update={"purpose": "debug_reference_selection"})

    def compile(self, request, *, debug_reference_selection=None):
        self.selection = debug_reference_selection
        return super().compile(request)


class _QueuedGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def generate(self, prompt):
        self.prompts.append(prompt)
        return ModelPlanningResponse(
            provider="test",
            model="deterministic-fake",
            response_id=f"response-{len(self.prompts)}",
            plan=self.responses.pop(0),
        )


class _Validator:
    def __init__(self, validation):
        self.validation = validation

    def validate(self, plan, request):
        return self.validation


class _InputValidator:
    def __init__(self, result=None):
        self.result = result or IntegrityValidationResult(status="pass", manifest={})
        self.calls = 0

    def validate(self, request):
        self.calls += 1
        return self.result


class PlannerFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.handoff = self.root / "drawing-planning-handoff.json"
        self.handoff.write_text("{}", encoding="utf-8")
        self.request = PlanningRequest(
            handoff_manifest_path=str(self.handoff),
            handoff_manifest_sha256=_SHA,
            planner_profile="production",
            publication_directory=str(self.root),
        )
        self.plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "plan_id": "VP-test-1",
            "views": [
                {
                    "id": "front",
                    "type": "model_view",
                    "center_marks": [{"id": "cm-1"}],
                    "symmetry_centerlines": [],
                    "label": None,
                }
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_current_registry_supports_c4_center_elements(self):
        self.plan["views"][0]["symmetry_centerlines"] = [{"id": "cl-1"}]
        assessment = current_registry().assess(self.plan)
        self.assertEqual(assessment.status, "supported")
        self.assertEqual(assessment.manifest_version, "1.0.0")
        self.assertEqual(assessment.unsupported_capabilities, ())

    def test_current_registry_supports_persisted_basic_views(self):
        plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "views": [
                {
                    "id": "front",
                    "type": "model_view",
                    "center_marks": [],
                    "symmetry_centerlines": [],
                    "label": None,
                },
                {
                    "id": "right",
                    "type": "projected_view",
                    "center_marks": [],
                    "symmetry_centerlines": [],
                    "label": None,
                },
            ],
        }
        assessment = current_registry().assess(plan)
        self.assertEqual(assessment.status, "supported")
        self.assertEqual(assessment.manifest_version, "1.0.0")
        self.assertEqual(assessment.unsupported_capabilities, ())

    def test_current_registry_supports_persisted_c2_local_views(self):
        plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "views": [
                {
                    "id": "local-cut",
                    "type": "broken_out_section",
                    "center_marks": [],
                    "symmetry_centerlines": [],
                    "label": None,
                },
                {
                    "id": "detail-a",
                    "type": "detail_view",
                    "center_marks": [],
                    "symmetry_centerlines": [],
                    "label": {
                        "text": "A",
                        "show": True,
                        "position_mode": "explicit",
                    },
                },
            ],
        }
        assessment = current_registry().assess(plan)
        self.assertEqual(assessment.status, "supported")
        self.assertEqual(assessment.manifest_version, "1.0.0")
        self.assertEqual(assessment.unsupported_capabilities, ())

    def test_current_registry_assesses_c3_auxiliary_constraints(self):
        plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "views": [
                {
                    "id": "aux-a",
                    "type": "auxiliary_view",
                    "center_marks": [],
                    "symmetry_centerlines": [],
                    "auxiliary_definition": {"show_arrow": True},
                    "label": {
                        "text": "A",
                        "show": True,
                        "position_mode": "document_default",
                    },
                }
            ],
        }
        assessment = current_registry().assess(plan)
        self.assertEqual(assessment.status, "supported")
        self.assertEqual(assessment.manifest_version, "1.0.0")

        plan["views"][0]["auxiliary_definition"]["show_arrow"] = False
        plan["views"][0]["label"]["position_mode"] = "explicit"
        assessment = current_registry().assess(plan)
        self.assertEqual(assessment.status, "capability_blocked")
        self.assertEqual(
            assessment.unsupported_capabilities,
            ("view_type.auxiliary_view.hidden_arrow",),
        )

    def test_engine_publishes_engineering_valid_plan_even_when_execution_is_blocked(self):
        self.plan["views"][0]["type"] = "auxiliary_view"
        self.plan["views"][0]["auxiliary_definition"] = {"show_arrow": False}
        compiler = _Compiler()
        gateway = _Gateway(self.plan)
        engine = PlannerEngine(
            prompt_compiler=compiler,
            model_gateway=gateway,
            validator=_Validator(_passing_validation()),
            capabilities=current_registry(),
            plan_store=PlanStore(),
            input_validator=_InputValidator(),
        )
        result = asyncio.run(engine.plan(self.request))
        self.assertEqual(result.status, "published")
        self.assertEqual(result.execution_readiness, "capability_blocked")
        self.assertEqual(result.audit.capability_manifest_version, "1.0.0")
        self.assertEqual(len(result.audit.request_sha256), 64)
        self.assertEqual(len(result.audit.candidate_sha256), 64)
        self.assertEqual(result.plan.path, str((self.root / "view_plan.json").resolve()))
        self.assertTrue((self.root / "view_plan.json").is_file())
        self.assertEqual(
            hashlib.sha256((self.root / "view_plan.json").read_bytes()).hexdigest(),
            result.plan.sha256,
        )
        self.assertEqual(compiler.calls, 1)
        self.assertEqual(gateway.calls, 1)

    def test_debug_engine_routes_references_before_generating_view_plan(self):
        request = PlanningRequest(
            handoff_manifest_path=str(self.handoff),
            handoff_manifest_sha256=_SHA,
            planner_profile="debug",
            debug_prompt_directory=str(self.root),
            publication_directory=str(self.root),
        )
        selection = {
            "category_references": ["references/categories/shaft.md"],
            "feature_references": ["references/features/overall.md"],
            "deferred_references": [],
        }
        compiler = _RoutingCompiler()
        gateway = _QueuedGateway((selection, self.plan))
        engine = PlannerEngine(
            prompt_compiler=compiler,
            model_gateway=gateway,
            validator=_Validator(_passing_validation()),
            capabilities=current_registry(),
            plan_store=PlanStore(),
            input_validator=_InputValidator(),
        )

        result = asyncio.run(engine.plan(request))

        self.assertEqual(result.status, "published")
        self.assertEqual(compiler.route_calls, 1)
        self.assertEqual(compiler.calls, 1)
        self.assertEqual(compiler.selection, selection)
        self.assertEqual(
            [prompt.purpose for prompt in gateway.prompts],
            ["debug_reference_selection", "view_plan"],
        )

    def test_engine_rejects_before_publication_when_a_deterministic_gate_fails(self):
        validation = PlanningValidation(
            integrity="pass",
            schema_check="pass",
            semantics="pass",
            coverage="fail",
            layout="pass",
            issues=(
                ValidationIssue(
                    code="VP-COVERAGE-001",
                    gate="coverage",
                    message="required internal feature is not expressed",
                    json_pointer="/feature_coverage/0",
                ),
            ),
        )
        engine = PlannerEngine(
            prompt_compiler=_Compiler(),
            model_gateway=_Gateway(self.plan),
            validator=_Validator(validation),
            capabilities=current_registry(),
            plan_store=PlanStore(),
            input_validator=_InputValidator(),
        )
        result = asyncio.run(engine.plan(self.request))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.execution_readiness, "not_assessed")
        self.assertFalse((self.root / "view_plan.json").exists())
        self.assertIsNone(result.audit.capability_manifest_version)

    def test_engine_rejects_invalid_handoff_before_prompt_or_model(self):
        compiler = _Compiler()
        gateway = _Gateway(self.plan)
        input_validator = _InputValidator(
            IntegrityValidationResult(
                status="fail",
                manifest=None,
                issues=(
                    ValidationIssue(
                        code="VP-INTEGRITY-ARTIFACT-HASH",
                        gate="integrity",
                        message="artifact changed",
                    ),
                ),
            )
        )
        engine = PlannerEngine(
            prompt_compiler=compiler,
            model_gateway=gateway,
            validator=_Validator(_passing_validation()),
            capabilities=current_registry(),
            plan_store=PlanStore(),
            input_validator=input_validator,
        )
        result = asyncio.run(engine.plan(self.request))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.validation.integrity, "fail")
        self.assertEqual(result.validation.schema_check, "not_run")
        self.assertIsNone(result.prompt_provenance)
        self.assertIsNone(result.audit.candidate_sha256)
        self.assertEqual(compiler.calls, 0)
        self.assertEqual(gateway.calls, 0)

    def test_plan_store_refuses_overwrite(self):
        store = PlanStore()
        store.publish(self.plan, str(self.root))
        with self.assertRaises(FileExistsError):
            store.publish(self.plan, str(self.root))
        self.assertEqual(list(self.root.glob(".view_plan.*.tmp")), [])

    def test_request_rejects_noncanonical_handoff_name(self):
        with self.assertRaises(ValueError):
            PlanningRequest(
                handoff_manifest_path=str(self.root / "other.json"),
                handoff_manifest_sha256=_SHA,
                publication_directory=str(self.root),
            )


def _passing_validation():
    return PlanningValidation(
        integrity="pass",
        schema_check="pass",
        semantics="pass",
        coverage="pass",
        layout="pass",
    )


if __name__ == "__main__":
    unittest.main()
