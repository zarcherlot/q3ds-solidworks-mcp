"""Run an immutable G0 request through the private read-only C# endpoint."""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from drawing_layout_planner.g0_matrix import (  # noqa: E402
    build_matrix_summary,
    build_probe_request,
    file_sha256,
    publish_json_once,
    validate_matrix_request,
)


def _post(url: str, value: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"G0 endpoint returned HTTP {error.code}: {detail}") from error
    if not isinstance(result, dict) or result.get("status") != "pass":
        raise RuntimeError("G0 case did not pass: " + json.dumps(result, ensure_ascii=False))
    return result


def _validate_loopback_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("execution base URL must be an unauthenticated HTTP origin")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("execution base URL must not contain a path, query, or fragment")
    if parsed.port is None or not parsed.hostname:
        raise ValueError("execution base URL must include a loopback port")
    if parsed.hostname.lower() != "localhost":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError("execution base URL must be loopback-only") from error
        if not address.is_loopback:
            raise ValueError("execution base URL must be loopback-only")
    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix-request", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--execution-base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    args = parser.parse_args()

    request_value = json.loads(args.matrix_request.read_text(encoding="utf-8"))
    validate_matrix_request(request_value)
    source_hashes_before = {
        artifact["path"]: artifact["sha256"]
        for case in request_value["cases"]
        for role, artifact in case["source"].items()
        if role != "kind"
    }
    endpoint = _validate_loopback_origin(args.execution_base_url) + (
        "/api/research/layout-boundary-probe"
    )
    reports: dict[str, dict] = {}
    for case in request_value["cases"]:
        probe_request = build_probe_request(case)
        result = _post(endpoint, probe_request, args.timeout_seconds)
        evidence_path = Path(result["evidence_path"]).resolve(strict=True)
        evidence_file_hash = file_sha256(evidence_path)
        if evidence_file_hash != result["evidence_sha256"]:
            raise RuntimeError("case evidence hash mismatch: " + case["case_id"])
        reports[case["case_id"]] = {
            "evidence": json.loads(evidence_path.read_text(encoding="utf-8")),
            "evidence_path": str(evidence_path),
            "evidence_file_sha256": evidence_file_hash,
        }

    for path, expected_hash in source_hashes_before.items():
        if file_sha256(path) != expected_hash:
            raise RuntimeError("frozen matrix source changed: " + path)
    summary = build_matrix_summary(request_value, reports)
    summary_file_hash = publish_json_once(args.summary_path, summary)
    print(
        json.dumps(
            {
                "status": summary["overall_status"],
                "summary_path": str(args.summary_path.resolve()),
                "summary_file_sha256": summary_file_hash,
                "coverage": summary["coverage"],
                "blockers": summary["blockers"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if summary["overall_status"] == "capability_blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
