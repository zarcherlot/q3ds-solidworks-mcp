"""Verify the repository Codex stdio MCP without calling any SolidWorks tool."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _verify(root: Path, timeout_seconds: float) -> dict[str, object]:
    schema_path = root / "adapters" / "claude" / "contracts" / "semantic-tools.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    expected = set(schema["required"])
    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "start_codex_mcp.ps1"),
        ],
        cwd=str(root),
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as diagnostics:
        with anyio.fail_after(timeout_seconds):
            async with stdio_client(parameters, errlog=diagnostics) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    discovered = {tool.name for tool in (await session.list_tools()).tools}
        diagnostics.seek(0)
        diagnostic_text = diagnostics.read()
    missing = sorted(expected - discovered)
    unexpected = sorted(discovered - expected)
    return {
        "ok": initialized.serverInfo.name == "Q3DS SolidWorks Engineering"
        and not missing
        and not unexpected,
        "server_name": initialized.serverInfo.name,
        "expected_tools": sorted(expected),
        "discovered_tools": sorted(discovered),
        "missing_tools": missing,
        "unexpected_tools": unexpected,
        "diagnostics_present": bool(diagnostic_text.strip()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    result = anyio.run(_verify, root, args.timeout_seconds)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
