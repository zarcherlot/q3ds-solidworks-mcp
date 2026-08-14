"""Build a read-only, hash-bound F0 corpus manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.f0_corpus import (  # noqa: E402
    F0CorpusError,
    build_f0_corpus_manifest,
    verify_f0_corpus_manifest,
)
from dimension_planner.f0_evidence import F0_CAPABILITY_IDS  # noqa: E402


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    repository_root = args.repository_root.resolve()
    validation_dir = repository_root / "validation"
    if output_dir == input_dir or _within(output_dir, input_dir):
        raise F0CorpusError("output directory must not be the corpus or its descendant")
    if output_dir == validation_dir or _within(output_dir, validation_dir):
        raise F0CorpusError("output directory must not be validation or its descendant")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise F0CorpusError("output directory must be new or empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_f0_corpus_manifest(input_dir)
    issues = verify_f0_corpus_manifest(manifest)
    if issues:
        raise F0CorpusError("corpus changed while hashing: " + "; ".join(issues))
    output_path = output_dir / "dimension-f0-corpus.json"
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    request_directory = output_dir / "probe-requests"
    request_directory.mkdir()
    drawing_template = manifest["templates"][0]
    for pair in manifest["pairs"]:
        request = {
            "protocol_id": "solidworks-dimension-api-probe",
            "schema_version": "1.0",
            "source": {
                "kind": "research_model_drawing_pair",
                "source_model": {
                    "path": pair["source_model"]["path"],
                    "sha256": pair["source_model"]["sha256"],
                },
                "source_drawing": {
                    "path": pair["source_drawing"]["path"],
                    "sha256": pair["source_drawing"]["sha256"],
                },
                "drawing_template": {
                    "path": drawing_template["path"],
                    "sha256": drawing_template["sha256"],
                },
            },
            "publication_directory": str(
                (output_dir / "live" / pair["case_id"]).resolve()
            ),
            "required_solidworks_revision": "33.5.0",
            "capability_ids": list(F0_CAPABILITY_IDS),
        }
        (request_directory / f"{pair['case_id']}.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "ready",
                "manifest_path": str(output_path),
                "pair_count": len(manifest["pairs"]),
                "standalone_model_count": len(manifest["standalone_models"]),
                "template_count": len(manifest["templates"]),
                "excluded_temporary_count": len(
                    manifest["excluded_temporary_files"]
                ),
                "probe_request_directory": str(request_directory),
                "probe_request_count": len(manifest["pairs"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
