import asyncio
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import ImageContent, ToolUseContent


_ROOT = Path(__file__).resolve().parents[3]
_ADAPTER = _ROOT / "adapters" / "claude"
os.sys.path.insert(0, str(_ROOT))
os.sys.path.insert(0, str(_ADAPTER))

from planning_sampling import McpSamplingPlanningModelGateway  # noqa: E402
from drawing_planner.model_gateway import PlanningModelUnavailable  # noqa: E402
from drawing_planner.planning_models import (  # noqa: E402
    CompiledPlanningPrompt,
    PlanningInputArtifact,
)


_SHA = "a" * 64


class _SamplingContext:
    def __init__(self, plan, tool_name="submit_solidworks_view_plan"):
        self.plan = plan
        self.tool_name = tool_name
        self.calls = []

    async def sample_step(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            response=SimpleNamespace(model="client-planner-model"),
            tool_calls=[
                ToolUseContent(
                    type="tool_use",
                    id="sampling-call-1",
                    name=self.tool_name,
                    input=self.plan,
                )
            ],
        )


class McpSamplingPlanningModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = []
        for kind, name in (
            ("handoff_manifest", "drawing-planning-handoff.json"),
            ("readiness_report", "drawing-readiness.json"),
            ("geometry_report", "model-geometry.json"),
        ):
            path = self.root / name
            path.write_text('{"status":"ready"}', encoding="utf-8")
            self.artifacts.append(
                PlanningInputArtifact(
                    kind=kind,
                    path=str(path),
                    sha256=_sha(path),
                    media_type="application/json",
                )
            )
        for view in ("front", "back", "left", "right", "top", "bottom"):
            path = self.root / f"{view}.png"
            path.write_bytes(b"png-" + view.encode("ascii"))
            self.artifacts.append(
                PlanningInputArtifact(
                    kind="standard_view_image",
                    path=str(path),
                    sha256=_sha(path),
                    media_type="image/png",
                    view=view,
                )
            )
        self.schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["protocol_id", "schema_version", "plan_id"],
            "properties": {
                "protocol_id": {"const": "solidworks-view-plan"},
                "schema_version": {"const": "1.4"},
                "plan_id": {"type": "string"},
            },
        }
        self.prompt = CompiledPlanningPrompt(
            planner_profile="production",
            messages=(
                {"role": "system", "content": "immutable system policy"},
                {"role": "user", "content": "plan the drawing views"},
            ),
            response_schema=self.schema,
            artifacts=tuple(self.artifacts),
            core_policy_sha256=_SHA,
            prompt_pack_sha256=_SHA,
            schema_sha256=_SHA,
            input_manifest_sha256=self.artifacts[0].sha256,
            envelope_sha256=_SHA,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_submits_exact_schema_and_complete_verified_evidence(self):
        plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "plan_id": "VP-sampling-1",
        }
        context = _SamplingContext(plan)
        response = asyncio.run(
            McpSamplingPlanningModelGateway(context).generate(self.prompt)
        )

        self.assertEqual(response.provider, "mcp-sampling")
        self.assertEqual(response.model, "client-planner-model")
        self.assertEqual(response.response_id, "sampling-call-1")
        self.assertEqual(response.plan, plan)
        self.assertEqual(len(context.calls), 1)
        call = context.calls[0]
        self.assertEqual(call["system_prompt"], "immutable system policy")
        self.assertEqual(call["tool_choice"], "required")
        self.assertFalse(call["execute_tools"])
        self.assertEqual(call["tools"][0].parameters, self.schema)
        blocks = call["messages"][0].content
        self.assertEqual(sum(isinstance(block, ImageContent) for block in blocks), 6)
        combined_text = "\n".join(
            block.text for block in blocks if hasattr(block, "text")
        )
        self.assertIn("drawing-planning-handoff.json", combined_text)
        self.assertIn("model-geometry.json", combined_text)

    def test_artifact_hash_drift_stops_before_sampling(self):
        context = _SamplingContext({})
        Path(self.artifacts[1].path).write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(
            PlanningModelUnavailable, "changed after prompt compilation"
        ):
            asyncio.run(
                McpSamplingPlanningModelGateway(context).generate(self.prompt)
            )
        self.assertEqual(context.calls, [])

    def test_reference_router_uses_distinct_submission_tool(self):
        selection = {
            "category_references": ["references/shaft.md"],
            "feature_references": ["references/overall.md"],
            "deferred_references": [],
        }
        prompt = self.prompt.model_copy(
            update={
                "purpose": "debug_reference_selection",
                "planner_profile": "debug",
                "response_schema": {
                    "type": "object",
                    "additionalProperties": False,
                },
            }
        )
        context = _SamplingContext(
            selection, tool_name="submit_debug_reference_selection"
        )

        response = asyncio.run(
            McpSamplingPlanningModelGateway(context).generate(prompt)
        )

        self.assertEqual(response.plan, selection)
        self.assertEqual(
            context.calls[0]["tools"][0].name,
            "submit_debug_reference_selection",
        )
        self.assertEqual(context.calls[0]["max_tokens"], 4096)

    def test_final_debug_prompt_attaches_selected_jpeg_reference(self):
        image = self.root / "shaft-reference.jpg"
        image.write_bytes(b"jpeg-reference")
        reference = PlanningInputArtifact(
            kind="debug_reference_image",
            path=str(image),
            sha256=_sha(image),
            media_type="image/jpeg",
        )
        prompt = CompiledPlanningPrompt.model_validate(
            {
                **self.prompt.model_dump(),
                "planner_profile": "debug",
                "artifacts": (
                    *(artifact.model_dump() for artifact in self.prompt.artifacts),
                    reference.model_dump(),
                ),
            }
        )
        plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "plan_id": "VP-debug-image-1",
        }
        context = _SamplingContext(plan)

        response = asyncio.run(
            McpSamplingPlanningModelGateway(context).generate(prompt)
        )

        self.assertEqual(response.plan, plan)
        blocks = context.calls[0]["messages"][0].content
        image_blocks = [block for block in blocks if isinstance(block, ImageContent)]
        self.assertEqual(len(image_blocks), 7)
        self.assertEqual(image_blocks[-1].mimeType, "image/jpeg")
        combined_text = "\n".join(
            block.text for block in blocks if hasattr(block, "text")
        )
        self.assertIn("selected debug reference image", combined_text)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
