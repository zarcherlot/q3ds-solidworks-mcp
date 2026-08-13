"""Strict immutable domain models for DimensionPlan 1.0.

F2 owns contracts and publication primitives only.  Deterministic engineering
validation is introduced in F3 and native creation remains an F4 concern.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

from drawing_planner.planning_models import canonical_json_sha256, json_object_copy


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
PlannerProfile = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")
]
SemanticVersion = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"),
]
Rfc3339DateTime = Annotated[
    str,
    StringConstraints(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
    ),
]

DimensionKind = Literal[
    "linear",
    "aligned",
    "diameter",
    "radius",
    "angular",
    "reference",
    "hole_diameter",
    "hole_depth",
    "hole_quantity",
    "hole_spacing",
    "hole_group_location",
    "overall",
    "step",
    "boss",
    "slot",
    "chamfer",
    "fillet",
    "symmetric",
]
QuantityKind = Literal["length", "angle", "count"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, populate_by_name=True
    )


class ArtifactBinding(StrictModel):
    path: str = Field(min_length=1)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _absolute_path(value, "artifact path")


class DimensionProducer(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    version: SemanticVersion
    ruleset_id: StableId
    ruleset_sha256: Sha256


class DimensionExecutionPolicy(StrictModel):
    on_integrity_mismatch: Literal["fail"] = "fail"
    on_selection_ambiguity: Literal["fail"] = "fail"
    on_unsupported_dimension: Literal["fail"] = "fail"
    on_layout_violation: Literal["fail"] = "fail"
    allow_source_model_write: Literal[False] = False
    allow_upstream_drawing_overwrite: Literal[False] = False
    allow_partial_commit: Literal[False] = False


class ModelOrPmiSource(StrictModel):
    source_tier: Literal["model_or_pmi"]
    handoff_collection: Literal[
        "model_driven_dimensions", "pmi_annotations", "manufacturing_features"
    ]
    source_ids: tuple[StableId, ...] = Field(min_length=1)

    @field_validator("source_ids", mode="before")
    @classmethod
    def freeze_source_ids(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("source_ids")
    @classmethod
    def unique_source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "source_ids")


class UserConfirmedSource(StrictModel):
    source_tier: Literal["user_confirmed_input"]
    approved_input_ids: tuple[StableId, ...] = Field(min_length=1)

    @field_validator("approved_input_ids", mode="before")
    @classmethod
    def freeze_approved_input_ids(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("approved_input_ids")
    @classmethod
    def unique_input_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "approved_input_ids")


class ReferenceMeasurementSource(StrictModel):
    source_tier: Literal["reference_geometry_measurement"]
    measurement_ids: tuple[StableId, ...] = Field(min_length=1)
    manufacturing_requirement: Literal[False] = False

    @field_validator("measurement_ids", mode="before")
    @classmethod
    def freeze_measurement_ids(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("measurement_ids")
    @classmethod
    def unique_measurement_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "measurement_ids")


DimensionSource = Annotated[
    ModelOrPmiSource | UserConfirmedSource | ReferenceMeasurementSource,
    Field(discriminator="source_tier"),
]


class DimensionValue(StrictModel):
    value_mode: Literal["model_driven", "approved_value", "measured_reference"]
    quantity_kind: QuantityKind
    nominal_si: FiniteFloat = Field(gt=0)

    @model_validator(mode="after")
    def validate_count(self) -> "DimensionValue":
        if self.quantity_kind == "count" and (
            self.nominal_si < 1 or not self.nominal_si.is_integer()
        ):
            raise ValueError("count values must be positive whole numbers")
        return self


class DimensionTolerance(StrictModel):
    kind: Literal["bilateral", "unilateral", "limit", "fit"]
    lower_si: FiniteFloat | None = None
    upper_si: FiniteFloat | None = None
    fit_code: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_form(self) -> "DimensionTolerance":
        if self.kind == "fit":
            if self.fit_code is None or self.lower_si is not None or self.upper_si is not None:
                raise ValueError("fit tolerance requires only fit_code")
        elif self.fit_code is not None or self.lower_si is None or self.upper_si is None:
            raise ValueError("numeric tolerance requires lower_si and upper_si only")
        elif self.lower_si > self.upper_si:
            raise ValueError("tolerance lower_si must not exceed upper_si")
        return self


class DimensionDisplayFormat(StrictModel):
    unit: Literal["document_default", "mm", "inch", "degree", "count"]
    precision: int = Field(ge=0, le=8)
    prefix: str = Field(default="", max_length=128)
    suffix: str = Field(default="", max_length=128)
    show_parentheses: bool = False
    show_units: bool = False
    dual_units: Literal[False] = False


class DimensionAttachment(StrictModel):
    attachment_id: StableId
    entity_id: StableId
    model_persistent_reference: str = Field(
        min_length=1, pattern=r"^[A-Za-z0-9+/]+={0,2}$"
    )
    persistent_reference_kind: Literal["entity", "backing_face"]
    role: Literal[
        "first",
        "second",
        "arc",
        "center",
        "leader",
        "feature",
        "symmetry_axis",
    ]


class DimensionHierarchy(StrictModel):
    level: Literal["functional", "manufacturing", "inspection", "reference"]
    priority: int = Field(ge=0, le=1000)
    chain_id: StableId | None = None
    baseline_id: StableId | None = None


class DimensionVerificationTolerance(StrictModel):
    value_abs_si: FiniteFloat = Field(ge=0)
    position_abs_m: FiniteFloat = Field(gt=0)
    attachment_count_exact: Literal[True] = True
    display_text_exact: bool


class PlannedDimension(StrictModel):
    dimension_id: StableId
    kind: DimensionKind
    source: DimensionSource
    target_view_id: StableId
    attachments: tuple[DimensionAttachment, ...] = Field(min_length=1)
    feature_ids: tuple[StableId, ...] = Field(min_length=1)
    value: DimensionValue
    tolerance: DimensionTolerance | None = None
    display_format: DimensionDisplayFormat
    dimension_zone_id: StableId
    hierarchy: DimensionHierarchy
    initial_position_sheet_m: tuple[FiniteFloat, FiniteFloat]
    verification_tolerance: DimensionVerificationTolerance

    @field_validator("attachments", "feature_ids", "initial_position_sheet_m", mode="before")
    @classmethod
    def freeze_arrays(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("attachments")
    @classmethod
    def unique_attachments(
        cls, value: tuple[DimensionAttachment, ...]
    ) -> tuple[DimensionAttachment, ...]:
        ids = tuple(item.attachment_id for item in value)
        _unique(ids, "attachment IDs")
        return value

    @field_validator("feature_ids")
    @classmethod
    def unique_feature_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "feature_ids")

    @model_validator(mode="after")
    def validate_source_value_and_reference_semantics(self) -> "PlannedDimension":
        expected_mode = {
            "model_or_pmi": "model_driven",
            "user_confirmed_input": "approved_value",
            "reference_geometry_measurement": "measured_reference",
        }[self.source.source_tier]
        if self.value.value_mode != expected_mode:
            raise ValueError("value_mode must match the declared source tier")
        if self.source.source_tier == "reference_geometry_measurement":
            if self.kind != "reference" or self.hierarchy.level != "reference":
                raise ValueError(
                    "reference measurements may only produce reference-level dimensions"
                )
            if self.tolerance is not None:
                raise ValueError("reference measurements cannot define manufacturing tolerance")
        elif self.kind == "reference" and self.hierarchy.level != "reference":
            raise ValueError("reference dimensions must use reference hierarchy")
        if self.kind in {"angular"} and self.value.quantity_kind != "angle":
            raise ValueError("angular dimensions require angle quantities")
        if self.kind == "hole_quantity" and self.value.quantity_kind != "count":
            raise ValueError("hole_quantity requires a count value")
        if self.kind not in {"angular", "hole_quantity"} and (
            self.value.quantity_kind != "length"
        ):
            raise ValueError("this dimension kind requires a length value")
        return self


class DimensionPlan(StrictModel):
    schema_uri: Literal[
        "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json"
    ] = Field(alias="$schema")
    protocol_id: Literal["solidworks-dimension-plan"]
    schema_version: Literal["1.0"]
    plan_id: StableId
    created_at_utc: Rfc3339DateTime
    producer: DimensionProducer
    execution_policy: DimensionExecutionPolicy
    handoff: ArtifactBinding
    handoff_id: StableId
    source_model: ArtifactBinding
    source_drawing: ArtifactBinding
    view_plan: ArtifactBinding
    verification_sidecar: ArtifactBinding
    configuration: str = Field(min_length=1, max_length=256)
    dimensions: tuple[PlannedDimension, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()

    @field_validator("dimensions", "assumptions", "open_questions", mode="before")
    @classmethod
    def freeze_arrays(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("handoff")
    @classmethod
    def validate_handoff_name(cls, value: ArtifactBinding) -> ArtifactBinding:
        if os.path.basename(value.path).lower() != "dimension-planning-handoff.json":
            raise ValueError("handoff must be named dimension-planning-handoff.json")
        return value

    @field_validator("source_model")
    @classmethod
    def validate_model_suffix(cls, value: ArtifactBinding) -> ArtifactBinding:
        return _artifact_suffix(value, ".sldprt", "source_model")

    @field_validator("source_drawing")
    @classmethod
    def validate_drawing_suffix(cls, value: ArtifactBinding) -> ArtifactBinding:
        return _artifact_suffix(value, ".slddrw", "source_drawing")

    @field_validator("view_plan", "verification_sidecar")
    @classmethod
    def validate_json_suffix(cls, value: ArtifactBinding) -> ArtifactBinding:
        return _artifact_suffix(value, ".json", "JSON artifact")

    @field_validator("assumptions", "open_questions")
    @classmethod
    def nonempty_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("assumptions and open_questions cannot contain empty text")
        return value

    @model_validator(mode="after")
    def validate_plan_inventory(self) -> "DimensionPlan":
        _unique(tuple(item.dimension_id for item in self.dimensions), "dimension IDs")
        paths = (
            self.handoff.path,
            self.source_model.path,
            self.source_drawing.path,
            self.view_plan.path,
            self.verification_sidecar.path,
        )
        if len({os.path.normcase(path) for path in paths}) != len(paths):
            raise ValueError("DimensionPlan artifact paths must be distinct")
        if self.open_questions:
            raise ValueError("a frozen DimensionPlan cannot contain open_questions")
        return self

    def execution_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.execution_dict(), "DimensionPlan")


class DimensionPlanningRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    handoff_path: str = Field(min_length=1)
    handoff_sha256: Sha256
    planner_profile: PlannerProfile = "production"
    publication_directory: str = Field(min_length=1)
    user_requirements: dict[str, Any] = Field(default_factory=dict)

    @field_validator("handoff_path")
    @classmethod
    def validate_handoff_path(cls, value: str) -> str:
        normalized = _absolute_path(value, "handoff_path")
        if os.path.basename(normalized).lower() != "dimension-planning-handoff.json":
            raise ValueError("handoff_path must be named dimension-planning-handoff.json")
        return normalized

    @field_validator("publication_directory")
    @classmethod
    def validate_publication_directory(cls, value: str) -> str:
        return _absolute_path(value, "publication_directory")

    @field_validator("user_requirements")
    @classmethod
    def validate_user_requirements(cls, value: dict[str, Any]) -> dict[str, Any]:
        return json_object_copy(value, "dimension user_requirements")


class PublishedDimensionPlan(StrictModel):
    plan_id: StableId
    path: str = Field(min_length=1)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = _absolute_path(value, "published dimension plan path")
        if os.path.basename(normalized).lower() != "dimension_plan.json":
            raise ValueError("published DimensionPlan must be named dimension_plan.json")
        return normalized


DimensionGateStatus = Literal["pass", "fail", "not_run"]


class DimensionValidationIssue(StrictModel):
    code: StableId
    gate: Literal[
        "integrity",
        "schema",
        "source",
        "attachment",
        "semantics",
        "coverage",
        "redundancy",
        "layout",
        "capability",
    ]
    message: str = Field(min_length=1, max_length=2000)
    json_pointer: str | None = Field(default=None, max_length=1000)


class DimensionPlanningValidation(StrictModel):
    integrity: DimensionGateStatus
    schema_check: DimensionGateStatus
    source: DimensionGateStatus
    attachment: DimensionGateStatus
    semantics: DimensionGateStatus
    coverage: DimensionGateStatus
    redundancy: DimensionGateStatus
    layout: DimensionGateStatus
    capability: DimensionGateStatus
    issues: tuple[DimensionValidationIssue, ...] = ()

    @field_validator("issues", mode="before")
    @classmethod
    def freeze_issues(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @property
    def engineering_passed(self) -> bool:
        return all(
            value == "pass"
            for value in (
                self.integrity,
                self.schema_check,
                self.source,
                self.attachment,
                self.semantics,
                self.coverage,
                self.redundancy,
                self.layout,
            )
        ) and not any(issue.gate != "capability" for issue in self.issues)

    @model_validator(mode="after")
    def validate_issue_consistency(self) -> "DimensionPlanningValidation":
        failed = {
            gate
            for gate, value in (
                ("integrity", self.integrity),
                ("schema", self.schema_check),
                ("source", self.source),
                ("attachment", self.attachment),
                ("semantics", self.semantics),
                ("coverage", self.coverage),
                ("redundancy", self.redundancy),
                ("layout", self.layout),
                ("capability", self.capability),
            )
            if value == "fail"
        }
        issue_gates = {issue.gate for issue in self.issues}
        if failed != issue_gates:
            raise ValueError(
                "each failed dimension validation gate must have an issue and vice versa"
            )
        return self


class DimensionPlanningAudit(StrictModel):
    request_sha256: Sha256
    candidate_sha256: Sha256 | None = None
    capability_manifest_version: SemanticVersion | None = None


class DimensionPlanningResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["published", "rejected"]
    execution_readiness: Literal[
        "supported", "capability_blocked", "not_assessed"
    ]
    validation: DimensionPlanningValidation
    plan: PublishedDimensionPlan | None
    audit: DimensionPlanningAudit
    unsupported_capabilities: tuple[str, ...] = ()

    @field_validator("unsupported_capabilities", mode="before")
    @classmethod
    def freeze_unsupported_capabilities(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @model_validator(mode="after")
    def validate_result(self) -> "DimensionPlanningResult":
        if self.status == "published":
            if not self.validation.engineering_passed or self.plan is None:
                raise ValueError("published result requires engineering-valid plan")
            if self.execution_readiness == "not_assessed":
                raise ValueError("published result requires capability assessment")
            if (
                self.audit.candidate_sha256 is None
                or self.audit.capability_manifest_version is None
            ):
                raise ValueError("published result requires candidate and capability audit")
        elif self.plan is not None or self.validation.engineering_passed:
            raise ValueError("rejected result cannot contain an engineering-valid plan")
        elif self.execution_readiness != "not_assessed":
            raise ValueError("rejected result cannot claim execution readiness")
        elif self.audit.capability_manifest_version is not None:
            raise ValueError("rejected result cannot claim capability assessment")
        if self.execution_readiness == "supported":
            if self.validation.capability != "pass":
                raise ValueError("supported readiness requires a passed capability gate")
        elif self.execution_readiness == "capability_blocked":
            if self.validation.capability != "fail":
                raise ValueError("capability_blocked requires a failed capability gate")
        elif self.validation.capability != "not_run":
            raise ValueError("not_assessed readiness requires capability not_run")
        if self.execution_readiness == "capability_blocked":
            if not self.unsupported_capabilities:
                raise ValueError("capability_blocked requires unsupported capabilities")
        elif self.unsupported_capabilities:
            raise ValueError(
                "unsupported capabilities require capability_blocked readiness"
            )
        return self


def dimension_plan_from_mapping(candidate: Mapping[str, Any]) -> DimensionPlan:
    return DimensionPlan.model_validate(json_object_copy(candidate, "DimensionPlan"))


def _absolute_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not os.path.isabs(value):
        raise ValueError(f"{label} must be an absolute path")
    if any(char in value for char in ("*", "?", "[", "]")):
        raise ValueError(f"{label} must not contain wildcard characters")
    return os.path.abspath(value)


def _freeze_json_array(value: Any) -> Any:
    """Accept a JSON array at the boundary and retain it immutably in-domain."""
    return tuple(value) if isinstance(value, list) else value


def _unique(value: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must be unique")
    return value


def _artifact_suffix(
    value: ArtifactBinding, suffix: str, label: str
) -> ArtifactBinding:
    if os.path.splitext(value.path)[1].lower() != suffix:
        raise ValueError(f"{label} path must end with {suffix}")
    return value
