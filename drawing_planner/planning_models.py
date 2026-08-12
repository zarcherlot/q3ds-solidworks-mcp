"""Strict domain contracts for the repository-owned drawing PlannerEngine."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
]
PlannerProfile = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")
]
_DEBUG_PROMPT_DIRECTORY_ENV = "PLANNER_DEBUG_PROMPT_DIRECTORY"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PlanningRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    handoff_manifest_path: str = Field(min_length=1)
    handoff_manifest_sha256: Sha256
    planner_profile: PlannerProfile = "production"
    debug_prompt_directory: str | None = None
    publication_directory: str = Field(min_length=1)
    user_requirements: dict[str, Any] = Field(default_factory=dict)

    @field_validator("user_requirements")
    @classmethod
    def validate_user_requirements(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value, "user_requirements")

    @field_validator("handoff_manifest_path")
    @classmethod
    def validate_handoff_path(cls, value: str) -> str:
        normalized = _absolute_path(value, "handoff_manifest_path")
        if os.path.basename(normalized).lower() != "drawing-planning-handoff.json":
            raise ValueError(
                "handoff_manifest_path must be named drawing-planning-handoff.json"
            )
        return normalized

    @field_validator("debug_prompt_directory")
    @classmethod
    def validate_debug_prompt_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _absolute_path(value, "debug_prompt_directory")

    @model_validator(mode="after")
    def validate_debug_profile(self) -> "PlanningRequest":
        if self.planner_profile == "debug" and self.debug_prompt_directory is None:
            configured = os.getenv(_DEBUG_PROMPT_DIRECTORY_ENV, "").strip()
            if not configured:
                raise ValueError(
                    "debug planner_profile requires debug_prompt_directory or "
                    f"{_DEBUG_PROMPT_DIRECTORY_ENV}"
                )
            object.__setattr__(
                self,
                "debug_prompt_directory",
                _absolute_path(configured, _DEBUG_PROMPT_DIRECTORY_ENV),
            )
        if self.planner_profile != "debug" and self.debug_prompt_directory is not None:
            raise ValueError("debug_prompt_directory is only valid with planner_profile=debug")
        return self

    @field_validator("publication_directory")
    @classmethod
    def validate_publication_directory(cls, value: str) -> str:
        return _absolute_path(value, "publication_directory")


class CompiledPlanningPrompt(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["view_plan", "debug_reference_selection"] = "view_plan"
    planner_profile: PlannerProfile
    messages: tuple[dict[str, Any], ...] = Field(min_length=2)
    response_schema: dict[str, Any]
    artifacts: tuple["PlanningInputArtifact", ...] = Field(min_length=9)
    core_policy_sha256: Sha256
    prompt_pack_sha256: Sha256
    schema_sha256: Sha256
    input_manifest_sha256: Sha256
    envelope_sha256: Sha256

    @model_validator(mode="after")
    def validate_artifact_inventory(self) -> "CompiledPlanningPrompt":
        counts: dict[str, int] = {}
        image_views: set[str] = set()
        debug_images = []
        for artifact in self.artifacts:
            if artifact.kind == "debug_reference_image":
                debug_images.append(artifact)
            else:
                counts[artifact.kind] = counts.get(artifact.kind, 0) + 1
            if artifact.kind == "standard_view_image" and artifact.view is not None:
                image_views.add(artifact.view)
        if counts != {
            "handoff_manifest": 1,
            "readiness_report": 1,
            "geometry_report": 1,
            "standard_view_image": 6,
        }:
            raise ValueError("compiled prompt requires the complete nine-artifact handoff")
        if image_views != {"front", "back", "left", "right", "top", "bottom"}:
            raise ValueError("compiled prompt requires all six standard-view images")
        manifest = next(
            artifact for artifact in self.artifacts if artifact.kind == "handoff_manifest"
        )
        if manifest.sha256 != self.input_manifest_sha256:
            raise ValueError("compiled prompt manifest artifact hash does not match its binding")
        if len({os.path.normcase(item.path) for item in self.artifacts}) != len(
            self.artifacts
        ):
            raise ValueError("compiled prompt artifact paths must be unique")
        if debug_images and (
            self.planner_profile != "debug" or self.purpose != "view_plan"
        ):
            raise ValueError(
                "debug reference images are only valid in the final debug ViewPlan prompt"
            )
        return self


class PlanningInputArtifact(StrictModel):
    kind: Literal[
        "handoff_manifest",
        "readiness_report",
        "geometry_report",
        "standard_view_image",
        "debug_reference_image",
    ]
    path: str = Field(min_length=1)
    sha256: Sha256
    media_type: Literal["application/json", "image/png", "image/jpeg"]
    view: Literal["front", "back", "left", "right", "top", "bottom"] | None = None

    @field_validator("path")
    @classmethod
    def validate_artifact_path(cls, value: str) -> str:
        return _absolute_path(value, "artifact path")

    @model_validator(mode="after")
    def validate_kind(self) -> "PlanningInputArtifact":
        if self.kind == "standard_view_image":
            if self.view is None or self.media_type != "image/png":
                raise ValueError("standard-view images require a view and image/png media type")
            if os.path.splitext(self.path)[1].lower() != ".png":
                raise ValueError("standard-view image paths must end with .png")
        elif self.kind == "debug_reference_image":
            if self.view is not None or self.media_type not in {
                "image/png",
                "image/jpeg",
            }:
                raise ValueError(
                    "debug reference images require image/png or image/jpeg without a view"
                )
            suffix = os.path.splitext(self.path)[1].lower()
            expected = "image/png" if suffix == ".png" else "image/jpeg"
            if suffix not in {".png", ".jpg", ".jpeg"} or self.media_type != expected:
                raise ValueError("debug reference image extension and media type must match")
        elif self.view is not None or self.media_type != "application/json":
            raise ValueError("JSON planning artifacts cannot declare a standard view")
        elif os.path.splitext(self.path)[1].lower() != ".json":
            raise ValueError("JSON planning artifact paths must end with .json")
        return self


class ModelPlanningResponse(StrictModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    response_id: str | None = Field(default=None, max_length=256)
    plan: dict[str, Any]

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _json_object(value, "plan")


class ValidationIssue(StrictModel):
    code: StableId
    gate: Literal["integrity", "schema", "semantics", "coverage", "layout"]
    message: str = Field(min_length=1, max_length=2000)
    json_pointer: str | None = Field(default=None, max_length=1000)


class PlanningValidation(StrictModel):
    integrity: Literal["pass", "fail", "not_run"]
    schema_check: Literal["pass", "fail", "not_run"]
    semantics: Literal["pass", "fail", "not_run"]
    coverage: Literal["pass", "fail", "not_run"]
    layout: Literal["pass", "fail", "not_run"]
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return all(
            value == "pass"
            for value in (
                self.integrity,
                self.schema_check,
                self.semantics,
                self.coverage,
                self.layout,
            )
        ) and not self.issues

    @model_validator(mode="after")
    def validate_issue_consistency(self) -> "PlanningValidation":
        failed = {
            gate
            for gate, value in (
                ("integrity", self.integrity),
                ("schema", self.schema_check),
                ("semantics", self.semantics),
                ("coverage", self.coverage),
                ("layout", self.layout),
            )
            if value == "fail"
        }
        issue_gates = {issue.gate for issue in self.issues}
        if failed != issue_gates:
            raise ValueError("each failed validation gate must have an issue and vice versa")
        return self


class PromptProvenance(StrictModel):
    planner_profile: PlannerProfile
    core_policy_sha256: Sha256
    prompt_pack_sha256: Sha256
    schema_sha256: Sha256
    input_manifest_sha256: Sha256
    envelope_sha256: Sha256
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    response_id: str | None = Field(default=None, max_length=256)


class PublishedPlan(StrictModel):
    plan_id: StableId
    path: str = Field(min_length=1)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def validate_plan_path(cls, value: str) -> str:
        normalized = _absolute_path(value, "path")
        if os.path.basename(normalized).lower() != "view_plan.json":
            raise ValueError("published plan must be named view_plan.json")
        return normalized


class PlanningAudit(StrictModel):
    request_sha256: Sha256
    candidate_sha256: Sha256 | None = None
    capability_manifest_version: str | None = Field(
        default=None,
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$",
    )


class PlanningResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["published", "rejected"]
    execution_readiness: Literal[
        "supported", "capability_blocked", "not_assessed"
    ]
    validation: PlanningValidation
    plan: PublishedPlan | None
    prompt_provenance: PromptProvenance | None
    audit: PlanningAudit
    unsupported_capabilities: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "PlanningResult":
        if self.status == "published":
            if not self.validation.passed or self.plan is None:
                raise ValueError("published result requires a valid published plan")
            if self.execution_readiness == "not_assessed":
                raise ValueError("published result requires capability assessment")
            if self.prompt_provenance is None or self.audit.candidate_sha256 is None:
                raise ValueError("published result requires model and candidate provenance")
            if self.audit.capability_manifest_version is None:
                raise ValueError("published result requires capability-manifest provenance")
        else:
            if self.plan is not None or self.validation.passed:
                raise ValueError("rejected result cannot contain a published valid plan")
            if self.execution_readiness != "not_assessed":
                raise ValueError("rejected result cannot claim execution readiness")
            if (self.prompt_provenance is None) != (self.audit.candidate_sha256 is None):
                raise ValueError(
                    "rejected result must record both prompt and candidate provenance or neither"
                )
            if self.audit.capability_manifest_version is not None:
                raise ValueError("rejected result cannot claim capability assessment")
        if self.execution_readiness == "capability_blocked":
            if not self.unsupported_capabilities:
                raise ValueError("capability_blocked requires unsupported capabilities")
        elif self.unsupported_capabilities:
            raise ValueError(
                "unsupported capabilities are only valid for capability_blocked results"
            )
        return self


class PlanPublicationResult(StrictModel):
    """Result of publishing one externally generated candidate through repository gates."""

    schema_version: Literal["1.0"] = "1.0"
    generation_mode: Literal["manual_skill"] = "manual_skill"
    ok: bool
    status: Literal["published", "rejected"]
    execution_readiness: Literal[
        "supported", "capability_blocked", "not_assessed"
    ]
    validation: PlanningValidation
    plan: PublishedPlan | None
    audit: PlanningAudit
    unsupported_capabilities: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "PlanPublicationResult":
        if self.ok != (self.status == "published"):
            raise ValueError("ok must match publication status")
        if self.status == "published":
            if not self.validation.passed or self.plan is None:
                raise ValueError("published result requires a valid published plan")
            if self.execution_readiness == "not_assessed":
                raise ValueError("published result requires capability assessment")
            if self.audit.candidate_sha256 is None:
                raise ValueError("published result requires a candidate hash")
            if self.audit.capability_manifest_version is None:
                raise ValueError("published result requires capability-manifest provenance")
        else:
            if self.plan is not None or self.validation.passed:
                raise ValueError("rejected result cannot contain a published valid plan")
            if self.execution_readiness != "not_assessed":
                raise ValueError("rejected result cannot claim execution readiness")
            if self.audit.candidate_sha256 is None:
                raise ValueError("rejected result requires a candidate hash")
            if self.audit.capability_manifest_version is not None:
                raise ValueError("rejected result cannot claim capability assessment")
        if self.execution_readiness == "capability_blocked":
            if not self.unsupported_capabilities:
                raise ValueError("capability_blocked requires unsupported capabilities")
        elif self.unsupported_capabilities:
            raise ValueError(
                "unsupported capabilities are only valid for capability_blocked results"
            )
        return self


def _absolute_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not os.path.isabs(value):
        raise ValueError(f"{label} must be an absolute path")
    if any(char in value for char in ("*", "?", "[", "]")):
        raise ValueError(f"{label} must not contain wildcard characters")
    return os.path.abspath(value)


def canonical_json_sha256(value: Any, label: str) -> str:
    payload = _canonical_json_bytes(value, label)
    return hashlib.sha256(payload).hexdigest()


def json_object_copy(value: Any, label: str) -> dict[str, Any]:
    payload = _canonical_json_bytes(value, label)
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _json_object(value: Any, label: str) -> dict[str, Any]:
    copied = json_object_copy(value, label)
    return copied


def _canonical_json_bytes(value: Any, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only finite JSON values") from exc
