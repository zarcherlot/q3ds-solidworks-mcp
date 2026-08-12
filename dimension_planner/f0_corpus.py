"""Read-only discovery and hashing for F0 model/drawing research corpora."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class F0CorpusError(ValueError):
    """Raised when a candidate F0 corpus is ambiguous or incomplete."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        "size_bytes": stat.st_size,
    }


def build_f0_corpus_manifest(root: Path | str) -> dict[str, Any]:
    """Discover exact-basename SLDPRT/SLDDRW pairs without opening CAD data."""

    corpus_root = Path(root).resolve()
    if not corpus_root.is_dir():
        raise F0CorpusError(f"corpus root is not an existing directory: {corpus_root}")

    files = sorted(
        (path for path in corpus_root.rglob("*") if path.is_file()),
        key=lambda path: str(path).casefold(),
    )
    excluded = [path for path in files if path.name.startswith("~$")]
    eligible = [path for path in files if path not in excluded]

    by_key: dict[tuple[str, str], dict[str, list[Path]]] = {}
    for path in eligible:
        extension = path.suffix.upper()
        if extension not in {".SLDPRT", ".SLDDRW"}:
            continue
        key = (str(path.parent.resolve()).casefold(), path.stem.casefold())
        by_key.setdefault(key, {}).setdefault(extension, []).append(path)

    pairs: list[dict[str, Any]] = []
    paired_paths: set[Path] = set()
    for key in sorted(by_key):
        extensions = by_key[key]
        models = extensions.get(".SLDPRT", [])
        drawings = extensions.get(".SLDDRW", [])
        if len(models) > 1 or len(drawings) > 1:
            raise F0CorpusError(f"ambiguous basename pair: {key[1]}")
        if len(models) == 1 and len(drawings) == 1:
            model = models[0]
            drawing = drawings[0]
            paired_paths.update((model, drawing))
            pairs.append(
                {
                    "case_id": "F0-" + hashlib.sha256(
                        str(model.resolve()).casefold().encode("utf-8")
                    ).hexdigest()[:12],
                    "name": model.stem,
                    "source_model": _artifact(model),
                    "source_drawing": _artifact(drawing),
                }
            )

    if not pairs:
        raise F0CorpusError("corpus contains no exact-basename SLDPRT/SLDDRW pairs")

    standalone_models = [
        _artifact(path)
        for path in eligible
        if path.suffix.upper() == ".SLDPRT" and path not in paired_paths
    ]
    standalone_drawings = [
        _artifact(path)
        for path in eligible
        if path.suffix.upper() == ".SLDDRW" and path not in paired_paths
    ]
    templates = [
        _artifact(path) for path in eligible if path.suffix.upper() == ".DRWDOT"
    ]
    if not templates:
        raise F0CorpusError("corpus must contain at least one DRWDOT template")

    known = {".SLDPRT", ".SLDDRW", ".DRWDOT"}
    unexpected = [_artifact(path) for path in eligible if path.suffix.upper() not in known]
    return {
        "protocol_id": "solidworks-dimension-api-corpus",
        "schema_version": "1.0",
        "root": str(corpus_root),
        "pairs": pairs,
        "standalone_models": standalone_models,
        "standalone_drawings": standalone_drawings,
        "templates": templates,
        "excluded_temporary_files": [_artifact(path) for path in excluded],
        "unexpected_files": unexpected,
    }


def verify_f0_corpus_manifest(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Re-hash every recorded artifact and return deterministic drift messages."""

    issues: list[str] = []
    artifact_groups: list[dict[str, Any]] = []
    for pair in manifest.get("pairs", []):
        artifact_groups.extend((pair["source_model"], pair["source_drawing"]))
    for key in (
        "standalone_models",
        "standalone_drawings",
        "templates",
        "excluded_temporary_files",
        "unexpected_files",
    ):
        artifact_groups.extend(manifest.get(key, []))
    for artifact in artifact_groups:
        path = Path(artifact["path"])
        if not path.is_file():
            issues.append("missing: " + str(path))
            continue
        actual = file_sha256(path)
        if actual.lower() != artifact["sha256"].lower():
            issues.append("sha256 drift: " + str(path))
    return tuple(issues)
