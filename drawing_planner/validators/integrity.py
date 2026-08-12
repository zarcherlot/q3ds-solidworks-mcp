"""Hash and path integrity gate for repository drawing-planning handoffs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from drawing_planner.feature_taxonomy import MechanicalFeatureTaxonomy, load_feature_taxonomy
from drawing_planner.planning_models import PlanningRequest, ValidationIssue
from drawing_planner.semantic_features import ModelSemanticFeatures


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
]
StandardView = Literal["front", "back", "left", "right", "top", "bottom"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Artifact(_StrictModel):
    path: str = Field(min_length=1)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        return _normalize_absolute_path(value)


class _ModelArtifact(_Artifact):
    configuration: str = Field(min_length=1)
    display_state: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def model_extension(cls, value: str) -> str:
        if Path(value).suffix.lower() != ".sldprt":
            raise ValueError("model path must end with .SLDPRT")
        return value


class _DrawingArtifact(_Artifact):
    blank: Literal[True]

    @field_validator("path")
    @classmethod
    def drawing_extension(cls, value: str) -> str:
        if Path(value).suffix.lower() != ".slddrw":
            raise ValueError("drawing path must end with .SLDDRW")
        return value


class _StandardViewImage(_Artifact):
    view: StandardView

    @field_validator("path")
    @classmethod
    def image_extension(cls, value: str) -> str:
        if Path(value).suffix.lower() != ".png":
            raise ValueError("standard-view image path must end with .PNG")
        return value


class _SemanticArtifact(_Artifact):
    @field_validator("path")
    @classmethod
    def json_extension(cls, value: str) -> str:
        if Path(value).suffix.lower() != ".json":
            raise ValueError("semantic artifact path must end with .json")
        return value


class _HandoffManifest(_StrictModel):
    protocol_id: Literal["q3ds-drawing-planning-handoff"]
    schema_version: Literal["1.0"]
    handoff_id: StableId
    status: Literal["ready"]
    model: _ModelArtifact
    blank_drawing: _DrawingArtifact
    readiness_report: _Artifact
    geometry_report: _Artifact
    semantic_features: _SemanticArtifact | None = None
    semantic_taxonomy: _SemanticArtifact | None = None
    standard_view_images: list[_StandardViewImage] = Field(min_length=6, max_length=6)
    drawing_context: dict
    blocking_issues: list[object] = Field(default_factory=list)
    open_questions: list[object] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_complete_handoff(self) -> "_HandoffManifest":
        expected = {"front", "back", "left", "right", "top", "bottom"}
        if {item.view for item in self.standard_view_images} != expected:
            raise ValueError("standard_view_images must contain each standard view exactly once")
        if self.blocking_issues or self.open_questions:
            raise ValueError("ready handoff cannot contain blocking issues or open questions")
        if Path(self.readiness_report.path).name.lower() != "drawing-readiness.json":
            raise ValueError("readiness report must be named drawing-readiness.json")
        if Path(self.geometry_report.path).name.lower() != "model-geometry.json":
            raise ValueError("geometry report must be named model-geometry.json")
        artifact_root = Path(self.blank_drawing.path).parent
        peers = [
            self.readiness_report.path,
            self.geometry_report.path,
            *(item.path for item in self.standard_view_images),
        ]
        if any(Path(path).parent != artifact_root for path in peers):
            raise ValueError(
                "blank drawing, reports and standard-view images must share one directory"
            )
        semantic = [item for item in (self.semantic_features, self.semantic_taxonomy) if item]
        if semantic and any(Path(item.path).parent != artifact_root for item in semantic):
            raise ValueError("semantic artifacts must share the initializer artifact directory")
        if (self.semantic_features is None) != (self.semantic_taxonomy is None):
            raise ValueError("semantic feature and taxonomy artifacts must be bound together")
        return self


class IntegrityValidationResult(_StrictModel):
    status: Literal["pass", "fail"]
    manifest: dict | None
    semantic_artifact: ModelSemanticFeatures | None = None
    issues: tuple[ValidationIssue, ...] = ()

    @model_validator(mode="after")
    def validate_consistency(self) -> "IntegrityValidationResult":
        if self.status == "pass" and (self.manifest is None or self.issues):
            raise ValueError("passing integrity result requires a manifest and no issues")
        if self.status == "fail" and (self.manifest is not None or not self.issues):
            raise ValueError("failed integrity result requires issues and no manifest")
        if self.status == "fail" and self.semantic_artifact is not None:
            raise ValueError("failed integrity result cannot expose a semantic artifact")
        return self


class HandoffIntegrityValidator:
    def validate(self, request: PlanningRequest) -> IntegrityValidationResult:
        manifest_path = Path(request.handoff_manifest_path)
        if not manifest_path.is_file():
            return _failure("VP-INTEGRITY-MANIFEST-MISSING", "handoff manifest does not exist")
        actual_manifest_hash = _file_sha256(manifest_path)
        if actual_manifest_hash != request.handoff_manifest_sha256:
            return _failure(
                "VP-INTEGRITY-MANIFEST-HASH",
                "handoff manifest SHA-256 does not match the planning request",
            )
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            manifest = _HandoffManifest.model_validate(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            return _failure("VP-INTEGRITY-MANIFEST-CONTRACT", str(exc))

        artifact_root = Path(manifest.blank_drawing.path).parent
        if not _same_path(str(manifest_path.parent), str(artifact_root)):
            return _failure(
                "VP-INTEGRITY-HANDOFF-LOCATION",
                "handoff manifest must share the initializer artifact directory",
            )
        if not _same_path(request.publication_directory, str(artifact_root)):
            return _failure(
                "VP-INTEGRITY-PUBLICATION-LOCATION",
                "view_plan.json must be published beside the initializer artifacts",
            )

        artifacts = [
            manifest.model,
            manifest.blank_drawing,
            manifest.readiness_report,
            manifest.geometry_report,
            *manifest.standard_view_images,
        ]
        if manifest.semantic_features is not None:
            artifacts.extend([manifest.semantic_features, manifest.semantic_taxonomy])
        for artifact in artifacts:
            path = Path(artifact.path)
            if not path.is_file():
                return _failure(
                    "VP-INTEGRITY-ARTIFACT-MISSING", f"handoff artifact does not exist: {path}"
                )
            if _file_sha256(path) != artifact.sha256:
                return _failure(
                    "VP-INTEGRITY-ARTIFACT-HASH", f"handoff artifact SHA-256 changed: {path}"
                )

        semantic_artifact = None
        if manifest.semantic_features is not None:
            assert manifest.semantic_taxonomy is not None
            try:
                taxonomy = load_feature_taxonomy(Path(manifest.semantic_taxonomy.path))
                semantic_raw = json.loads(
                    Path(manifest.semantic_features.path).read_text(encoding="utf-8-sig")
                )
                semantic_artifact = ModelSemanticFeatures.model_validate(semantic_raw)
            except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
                return _failure("VP-INTEGRITY-SEMANTIC-CONTRACT", str(exc))
            try:
                geometry = json.loads(
                    Path(manifest.geometry_report.path).read_text(encoding="utf-8-sig")
                )
                if not isinstance(geometry, Mapping):
                    raise ValueError("geometry report must contain a JSON object")
                _validate_semantic_bindings(manifest, semantic_artifact, taxonomy, geometry)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                return _failure("VP-INTEGRITY-SEMANTIC-BINDING", str(exc))
        return IntegrityValidationResult(
            status="pass",
            manifest=manifest.model_dump(mode="json"),
            semantic_artifact=semantic_artifact,
        )

    def validate_plan_bindings(
        self, plan: Mapping[str, Any], request: PlanningRequest
    ) -> IntegrityValidationResult:
        handoff = self.validate(request)
        if handoff.status == "fail":
            return handoff
        assert handoff.manifest is not None
        manifest = handoff.manifest
        expected = {
            "model_path": manifest["model"]["path"],
            "model_sha256": manifest["model"]["sha256"],
            "drawing_path": manifest["blank_drawing"]["path"],
            "drawing_sha256": manifest["blank_drawing"]["sha256"],
            "readiness_report_path": manifest["readiness_report"]["path"],
            "readiness_report_sha256": manifest["readiness_report"]["sha256"],
            "geometry_report_path": manifest["geometry_report"]["path"],
            "geometry_report_sha256": manifest["geometry_report"]["sha256"],
            "configuration": manifest["model"]["configuration"],
            "display_state": manifest["model"]["display_state"],
        }
        path_fields = {
            "model_path",
            "drawing_path",
            "readiness_report_path",
            "geometry_report_path",
        }
        for field, value in expected.items():
            actual = plan.get(field)
            matches = (
                _same_path(str(actual), str(value))
                if field in path_fields and isinstance(actual, str)
                else actual == value
            )
            if not matches:
                return _failure(
                    "VP-INTEGRITY-PLAN-BINDING",
                    f"view plan field {field} does not match the frozen handoff",
                )

        image_rows = plan.get("standard_view_images")
        if not isinstance(image_rows, list):
            return _failure(
                "VP-INTEGRITY-PLAN-IMAGES",
                "view plan standard_view_images must be an array",
            )
        plan_images = {
            row.get("view"): row
            for row in image_rows
            if isinstance(row, Mapping) and isinstance(row.get("view"), str)
        }
        handoff_images = {
            row["view"]: row for row in manifest["standard_view_images"]
        }
        if set(plan_images) != set(handoff_images):
            return _failure(
                "VP-INTEGRITY-PLAN-IMAGES",
                "view plan standard-view set does not match the frozen handoff",
            )
        for view, expected_image in handoff_images.items():
            actual_image = plan_images[view]
            if not _same_path(
                str(actual_image.get("path")), expected_image["path"]
            ) or actual_image.get("sha256") != expected_image["sha256"]:
                return _failure(
                    "VP-INTEGRITY-PLAN-IMAGES",
                    f"view plan image binding changed for standard view {view}",
                )

        context = manifest["drawing_context"]
        for field in (
            "sheet",
            "projection_method",
            "sheet_scale",
            "inner_frame",
            "reserved_zones",
        ):
            if field not in context or plan.get(field) != context[field]:
                return _failure(
                    "VP-INTEGRITY-DRAWING-CONTEXT",
                    f"view plan field {field} does not match handoff drawing_context",
                )
        return handoff


def _failure(code: str, message: str) -> IntegrityValidationResult:
    return IntegrityValidationResult(
        status="fail",
        manifest=None,
        issues=(
            ValidationIssue(
                code=code,
                gate="integrity",
                message=message[:2000],
            ),
        ),
    )


def _validate_semantic_bindings(
    manifest: _HandoffManifest,
    semantic: ModelSemanticFeatures,
    taxonomy: MechanicalFeatureTaxonomy,
    geometry: Mapping[str, Any],
) -> None:
    assert manifest.semantic_features is not None
    assert manifest.semantic_taxonomy is not None
    expected_model = manifest.model
    if not _same_path(semantic.model.path, expected_model.path):
        raise ValueError("semantic artifact model path does not match the frozen handoff")
    if semantic.model.sha256 != expected_model.sha256:
        raise ValueError("semantic artifact model SHA-256 does not match the frozen handoff")
    if semantic.model.configuration != expected_model.configuration:
        raise ValueError("semantic artifact configuration does not match the frozen handoff")
    if semantic.model.display_state != expected_model.display_state:
        raise ValueError("semantic artifact display state does not match the frozen handoff")
    if not _same_path(semantic.geometry_report.path, manifest.geometry_report.path):
        raise ValueError("semantic artifact geometry path does not match the frozen handoff")
    if semantic.geometry_report.sha256 != manifest.geometry_report.sha256:
        raise ValueError("semantic artifact geometry SHA-256 does not match the frozen handoff")
    if not _same_path(semantic.taxonomy.path, manifest.semantic_taxonomy.path):
        raise ValueError("semantic artifact taxonomy path does not match the frozen handoff")
    if semantic.taxonomy.sha256 != manifest.semantic_taxonomy.sha256:
        raise ValueError("semantic artifact taxonomy SHA-256 does not match the frozen handoff")
    semantic.validate_bindings(taxonomy, geometry)


def _normalize_absolute_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not os.path.isabs(value):
        raise ValueError("artifact path must be absolute")
    if any(char in value for char in ("*", "?", "[", "]")):
        raise ValueError("artifact path must not contain wildcard characters")
    return os.path.abspath(value)


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
