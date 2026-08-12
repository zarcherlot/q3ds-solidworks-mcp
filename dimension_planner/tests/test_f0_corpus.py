from __future__ import annotations

from pathlib import Path

import pytest

from dimension_planner.f0_corpus import (
    F0CorpusError,
    build_f0_corpus_manifest,
    verify_f0_corpus_manifest,
)


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def test_discovers_pairs_template_standalone_and_excludes_lock_file(tmp_path: Path):
    _write(tmp_path / "case-a" / "plate.SLDPRT", b"part")
    _write(tmp_path / "case-a" / "plate.SLDDRW", b"drawing")
    _write(tmp_path / "case-a" / "~$plate.SLDPRT", b"lock")
    _write(tmp_path / "case-b" / "extra.SLDPRT", b"extra")
    _write(tmp_path / "templates" / "A3.DRWDOT", b"template")

    manifest = build_f0_corpus_manifest(tmp_path)

    assert len(manifest["pairs"]) == 1
    assert manifest["pairs"][0]["name"] == "plate"
    assert len(manifest["standalone_models"]) == 1
    assert len(manifest["templates"]) == 1
    assert len(manifest["excluded_temporary_files"]) == 1
    assert verify_f0_corpus_manifest(manifest) == ()


def test_detects_hash_drift(tmp_path: Path):
    model = tmp_path / "case" / "plate.SLDPRT"
    _write(model, b"part")
    _write(tmp_path / "case" / "plate.SLDDRW", b"drawing")
    _write(tmp_path / "A3.DRWDOT", b"template")
    manifest = build_f0_corpus_manifest(tmp_path)

    model.write_bytes(b"changed")

    assert verify_f0_corpus_manifest(manifest) == ("sha256 drift: " + str(model),)


def test_requires_pair_and_template(tmp_path: Path):
    _write(tmp_path / "only.SLDPRT", b"part")
    with pytest.raises(F0CorpusError, match="no exact-basename"):
        build_f0_corpus_manifest(tmp_path)

    _write(tmp_path / "only.SLDDRW", b"drawing")
    with pytest.raises(F0CorpusError, match="DRWDOT"):
        build_f0_corpus_manifest(tmp_path)
