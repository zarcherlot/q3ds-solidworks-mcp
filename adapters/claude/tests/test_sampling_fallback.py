import asyncio
import hashlib
import json
import os
import tempfile
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler
from mcp.types import (
    CreateMessageResultWithTools,
    ImageContent,
    SamplingCapability,
    SamplingToolsCapability,
    ToolUseContent,
)


_ROOT = Path(__file__).resolve().parents[3]
_ADAPTER = _ROOT / "adapters" / "claude"
os.sys.path.insert(0, str(_ROOT))
os.sys.path.insert(0, str(_ADAPTER))

from config import (  # noqa: E402
    SamplingFallbackConfig,
    SamplingFallbackConfigurationError,
    load_sampling_fallback_config,
)
from planning_sampling import McpSamplingPlanningModelGateway  # noqa: E402
from sampling_fallback import (  # noqa: E402
    CredentialSafeSamplingHandler,
    build_sampling_fallback_handler,
)
from drawing_planner.planning_models import (  # noqa: E402
    CompiledPlanningPrompt,
    PlanningInputArtifact,
)


_SHA = "a" * 64


class SamplingFallbackConfigTests(unittest.TestCase):
    def test_disabled_by_default_without_credentials(self):
        config = load_sampling_fallback_config({})

        self.assertFalse(config.enabled)
        self.assertIsNone(config.api_key)
        self.assertIsNone(config.model)
        self.assertIsNone(build_sampling_fallback_handler(config))

    def test_enabled_configuration_requires_dedicated_key_and_model(self):
        with self.assertRaisesRegex(
            SamplingFallbackConfigurationError,
            "PLANNER_SAMPLING_API_KEY",
        ):
            load_sampling_fallback_config(
                {"PLANNER_SAMPLING_FALLBACK_ENABLED": "true"}
            )
        with self.assertRaisesRegex(
            SamplingFallbackConfigurationError,
            "PLANNER_SAMPLING_MODEL",
        ):
            load_sampling_fallback_config(
                {
                    "PLANNER_SAMPLING_FALLBACK_ENABLED": "true",
                    "PLANNER_SAMPLING_API_KEY": "dedicated-secret",
                }
            )

    def test_does_not_implicitly_reuse_openai_api_key(self):
        with self.assertRaisesRegex(
            SamplingFallbackConfigurationError,
            "PLANNER_SAMPLING_API_KEY",
        ):
            load_sampling_fallback_config(
                {
                    "PLANNER_SAMPLING_FALLBACK_ENABLED": "true",
                    "OPENAI_API_KEY": "must-not-be-reused",
                    "PLANNER_SAMPLING_MODEL": "planner-model",
                }
            )

    def test_normalizes_openai_compatible_base_url(self):
        config = load_sampling_fallback_config(
            {
                "PLANNER_SAMPLING_FALLBACK_ENABLED": "yes",
                "PLANNER_SAMPLING_API_KEY": " dedicated-secret ",
                "PLANNER_SAMPLING_MODEL": " planner-model ",
                "PLANNER_SAMPLING_BASE_URL": " https://models.example/v1/ ",
            }
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.api_key, "dedicated-secret")
        self.assertEqual(config.model, "planner-model")
        self.assertEqual(config.base_url, "https://models.example/v1")

    def test_rejects_ambiguous_boolean_and_non_http_endpoint(self):
        with self.assertRaises(SamplingFallbackConfigurationError):
            load_sampling_fallback_config(
                {"PLANNER_SAMPLING_FALLBACK_ENABLED": "sometimes"}
            )
        with self.assertRaises(SamplingFallbackConfigurationError):
            load_sampling_fallback_config(
                {"PLANNER_SAMPLING_BASE_URL": "file:///private/model"}
            )

    def test_builds_handler_without_making_a_network_request(self):
        handler = build_sampling_fallback_handler(
            SamplingFallbackConfig(
                enabled=True,
                api_key="dedicated-secret",
                model="planner-model",
                base_url="https://models.example/v1",
            )
        )

        self.assertIsInstance(handler, CredentialSafeSamplingHandler)


class CredentialSafeSamplingHandlerTests(unittest.TestCase):
    def test_provider_exception_and_traceback_do_not_expose_secret(self):
        secret = "sk-secret-that-must-not-escape"

        async def failing_delegate(*_args, **_kwargs):
            raise RuntimeError(f"provider rejected API key {secret}")

        handler = CredentialSafeSamplingHandler(failing_delegate)
        try:
            asyncio.run(handler([], None, None))
        except RuntimeError as exc:
            rendered = "".join(traceback.format_exception(exc))
            self.assertNotIn(secret, str(exc))
            self.assertNotIn(secret, rendered)
            self.assertIn("provider request failed", str(exc))
        else:
            self.fail("credential-safe handler did not propagate a sanitized failure")


class SamplingFallbackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts: list[PlanningInputArtifact] = []
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
                {"role": "system", "content": "immutable policy"},
                {"role": "user", "content": "plan the drawing"},
            ),
            response_schema=self.schema,
            artifacts=tuple(self.artifacts),
            core_policy_sha256=_SHA,
            prompt_pack_sha256=_SHA,
            schema_sha256=_SHA,
            input_manifest_sha256=self.artifacts[0].sha256,
            envelope_sha256=_SHA,
        )
        self.plan = {
            "protocol_id": "solidworks-view-plan",
            "schema_version": "1.4",
            "plan_id": "VP-server-fallback-1",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_client_without_sampling_uses_fallback_with_schema_and_images(self):
        fallback_calls = []

        async def fallback_handler(messages, params, _context):
            fallback_calls.append((messages, params))
            return _submission(self.plan, model="server-fallback-model")

        server = self._server(CredentialSafeSamplingHandler(fallback_handler))

        async def exercise():
            async with Client(server) as client:
                result = await client.call_tool("test_plan", {})
                return result.data

        result = asyncio.run(exercise())

        self.assertEqual(result["plan"], self.plan)
        self.assertEqual(result["model"], "server-fallback-model")
        self.assertEqual(len(fallback_calls), 1)
        messages, params = fallback_calls[0]
        self.assertEqual(params.toolChoice.mode, "required")
        self.assertEqual(len(params.tools), 1)
        self.assertEqual(params.tools[0].name, "submit_solidworks_view_plan")
        self.assertEqual(params.tools[0].inputSchema, self.schema)
        blocks = messages[0].content
        self.assertEqual(sum(isinstance(block, ImageContent) for block in blocks), 6)

    def test_sampling_tools_capable_client_remains_preferred(self):
        fallback_calls = []
        client_calls = []

        async def fallback_handler(*args):
            fallback_calls.append(args)
            return _submission(self.plan, model="unexpected-fallback")

        async def client_handler(messages, params, _context):
            client_calls.append((messages, params))
            return _submission(self.plan, model="client-model")

        server = self._server(CredentialSafeSamplingHandler(fallback_handler))

        async def exercise():
            async with Client(
                server,
                sampling_handler=client_handler,
                sampling_capabilities=SamplingCapability(
                    tools=SamplingToolsCapability()
                ),
            ) as client:
                result = await client.call_tool("test_plan", {})
                return result.data

        result = asyncio.run(exercise())

        self.assertEqual(result["model"], "client-model")
        self.assertEqual(len(client_calls), 1)
        self.assertEqual(fallback_calls, [])

    def test_openai_handler_converts_images_tools_and_structured_submission(self):
        api_calls = []

        async def create_completion(**kwargs):
            api_calls.append(kwargs)
            return SimpleNamespace(
                model="api-returned-model",
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="api-call-1",
                                    function=SimpleNamespace(
                                        name="submit_solidworks_view_plan",
                                        arguments=json.dumps(self.plan),
                                    ),
                                )
                            ],
                        ),
                    )
                ],
            )

        api_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create_completion)
            )
        )
        delegate = OpenAISamplingHandler(
            default_model="configured-planner-model",
            client=api_client,
        )
        server = self._server(CredentialSafeSamplingHandler(delegate))

        async def exercise():
            async with Client(server) as client:
                result = await client.call_tool("test_plan", {})
                return result.data

        result = asyncio.run(exercise())

        self.assertEqual(result["plan"], self.plan)
        self.assertEqual(result["model"], "api-returned-model")
        self.assertEqual(len(api_calls), 1)
        request = api_calls[0]
        self.assertEqual(request["model"], "configured-planner-model")
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(
            request["tools"][0]["function"]["parameters"],
            self.schema,
        )
        image_parts = [
            part
            for message in request["messages"]
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part["type"] == "image_url"
        ]
        self.assertEqual(len(image_parts), 6)

    def _server(self, fallback_handler):
        server = FastMCP(
            "sampling-fallback-test",
            sampling_handler=fallback_handler,
            sampling_handler_behavior="fallback",
        )
        prompt = self.prompt

        @server.tool(name="test_plan")
        async def test_plan(ctx: Context):
            response = await McpSamplingPlanningModelGateway(ctx).generate(prompt)
            return {"model": response.model, "plan": response.plan}

        return server


def _submission(plan: dict, *, model: str) -> CreateMessageResultWithTools:
    return CreateMessageResultWithTools(
        role="assistant",
        model=model,
        stopReason="toolUse",
        content=[
            ToolUseContent(
                type="tool_use",
                id="sampling-call-1",
                name="submit_solidworks_view_plan",
                input=plan,
            )
        ],
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
