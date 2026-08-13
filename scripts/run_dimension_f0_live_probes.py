"""Run repository-native F0 probes through the C# Execution Service.

This script is an orchestration client only. It never imports SolidWorks
Interop, pywin32, or COM. The dedicated C# research endpoint performs all CAD
operations on its STA thread and publishes the evidence report last.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.f0_evidence import (  # noqa: E402
    F0_CAPABILITY_IDS,
    F0CapabilityEvidenceError,
    evaluate_f0_evidence,
)


_STABLE_FAILURE_CAPABILITIES = {
    "linear_dimension",
    "diameter_dimension",
    "radius_dimension",
    "hole_callout",
    "chamfer_dimension",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _post_json(url: str, payload: bytes, timeout_seconds: int) -> dict:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"execution service returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"execution service is unavailable: {exc.reason}") from exc
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("execution service response must be a JSON object")
    return value


def _prepare_output(directory: Path) -> Path:
    output = directory.resolve()
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise RuntimeError(f"summary output directory must be new or empty: {output}")
    else:
        output.mkdir(parents=True)
    return output


def build_f0_run_matrix(evidence_rows: list[dict]) -> dict:
    """Aggregate validated per-case evidence without weakening promotion gates."""

    source_kind_counts: dict[str, int] = {}
    rows_by_capability = {capability_id: [] for capability_id in F0_CAPABILITY_IDS}
    for evidence in evidence_rows:
        source_kind = evidence["source_kind"]
        source_kind_counts[source_kind] = source_kind_counts.get(source_kind, 0) + 1
        for row in evidence["capabilities"]:
            rows_by_capability[row["id"]].append(row)

    capabilities = []
    for capability_id in F0_CAPABILITY_IDS:
        rows = rows_by_capability[capability_id]
        check_counts = {
            check: sum(bool(row["checks"][check]) for row in rows)
            for check in (
                "native_api_invoked",
                "in_memory_readback",
                "save_close_readonly_reopen",
                "stable_identity",
                "attachment_readback",
                "position_readback",
                "text_bounds_readback",
            )
        }
        stable_failures = 0
        for row in rows:
            for record in row["evidence"]:
                try:
                    parsed = json.loads(record)
                except (TypeError, json.JSONDecodeError):
                    continue
                failure = parsed.get("stable_failure")
                if isinstance(failure, dict) and failure.get(
                    "expected_failure_observed"
                ) is True:
                    stable_failures += 1
        declared_counts = {
            status: sum(row["status"] == status for row in rows)
            for status in ("supported", "planned", "unsupported")
        }
        positive_ready = all(
            check_counts[name] > 0
            for name in (
                "native_api_invoked",
                "in_memory_readback",
                "save_close_readonly_reopen",
                "stable_identity",
                "position_readback",
            )
        )
        failure_ready = (
            capability_id not in _STABLE_FAILURE_CAPABILITIES
            or stable_failures > 0
        )
        capabilities.append(
            {
                "id": capability_id,
                "case_count": len(rows),
                "declared_status_counts": declared_counts,
                "check_case_counts": check_counts,
                "stable_failure_case_count": stable_failures,
                "research_coverage": (
                    "covered"
                    if positive_ready and failure_ready
                    else "partial"
                ),
            }
        )

    frozen_count = source_kind_counts.get("frozen_viewplan_drawing", 0)
    research_coverage_complete = all(
        row["research_coverage"] == "covered"
        or row["declared_status_counts"]["unsupported"] > 0
        for row in capabilities
    )
    return {
        "source_kind_counts": source_kind_counts,
        "research_coverage_complete": research_coverage_complete,
        "production_frozen_case_count": frozen_count,
        "overall_status": (
            "complete"
            if research_coverage_complete and frozen_count > 0
            else "incomplete"
        ),
        "capabilities": capabilities,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-request-directory", type=Path, required=True)
    parser.add_argument("--summary-output-directory", type=Path, required=True)
    parser.add_argument(
        "--execution-service-url",
        default="http://localhost:5000/api/research/dimension-probe",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    request_directory = args.probe_request_directory.resolve()
    if not request_directory.is_dir():
        raise RuntimeError(
            f"probe request directory does not exist: {request_directory}"
        )
    output = _prepare_output(args.summary_output_directory)
    request_paths = sorted(request_directory.glob("*.json"), key=lambda path: path.name)
    if not request_paths:
        raise RuntimeError("probe request directory is empty")

    cases: list[dict] = []
    evidence_rows: list[dict] = []
    failed = False
    for request_path in request_paths:
        row: dict = {"request_path": str(request_path)}
        try:
            payload = request_path.read_bytes()
            source_request = json.loads(payload.decode("utf-8"))
            response = _post_json(
                args.execution_service_url, payload, args.timeout_seconds
            )
            row["service_response"] = response
            if response.get("status") != "evidence_ready":
                raise RuntimeError(
                    "execution service did not complete the probe lifecycle: "
                    + json.dumps(response, ensure_ascii=False)
                )
            report_value = response.get("report_path")
            if not isinstance(report_value, str) or not report_value:
                raise RuntimeError("execution service did not return report_path")
            report_path = Path(report_value).resolve()
            if not report_path.is_file():
                raise RuntimeError(f"evidence report does not exist: {report_path}")
            actual_report_hash = _sha256(report_path)
            expected_report_hash = response.get("report_sha256")
            if actual_report_hash != expected_report_hash:
                raise RuntimeError("evidence report SHA-256 does not match service response")
            evidence = json.loads(report_path.read_text(encoding="utf-8"))
            evaluation = evaluate_f0_evidence(
                evidence, source_request=source_request
            )
            evidence_rows.append(evidence)
            row["evaluation"] = {
                "overall_status": evaluation.overall_status,
                "evidence_sha256": evaluation.evidence_sha256,
                "capability_statuses": dict(evaluation.capability_statuses),
                "blockers": list(evaluation.blockers),
            }
            row["status"] = "evaluated"
        except (OSError, ValueError, RuntimeError, F0CapabilityEvidenceError) as exc:
            row["status"] = "failed"
            row["error"] = str(exc)
            failed = True
        cases.append(row)
        print(json.dumps(row, ensure_ascii=False))

    summary = {
        "protocol_id": "solidworks-dimension-api-probe-run-summary",
        "schema_version": "1.0",
        "case_count": len(cases),
        "failed_count": sum(item["status"] == "failed" for item in cases),
        "matrix": build_f0_run_matrix(evidence_rows),
        "cases": cases,
    }
    summary_path = output / "dimension-f0-live-probe-summary.json"
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(summary_path)
    print(
        json.dumps(
            {
                "status": "failed" if failed else "complete",
                "summary_path": str(summary_path),
                "summary_sha256": _sha256(summary_path),
            },
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
