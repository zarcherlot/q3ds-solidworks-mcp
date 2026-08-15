"""Strict immutable domain models for DrawingLayoutPlan 1.0.

G2 owns only the plan contract, capability boundary and publication primitive.
Coordinate solving is a G3 concern and native mutation remains exclusively G4.
"""

from __future__ import annotations

import math
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
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
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
BoundaryCapability = Literal[
    "view_outline_bounds",
    "dimension_display_bounds",
    "note_text_bounds",
    "leader_bounds",
    "view_label_bounds",
    "section_symbol_bounds",
    "center_element_bounds",
    "sheet_border_bounds",
    "title_block_bounds",
    "rebuild_drift",
    "save_reopen_drift",
]


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


class LayoutProducer(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    version: SemanticVersion
    ruleset_id: StableId
    ruleset_sha256: Sha256


class LayoutExecutionPolicy(StrictModel):
    on_integrity_mismatch: Literal["fail"] = "fail"
    on_layout_violation: Literal["fail"] = "fail"
    on_unsupported_operation: Literal["fail"] = "fail"
    preserve_dimension_count: Literal[True] = True
    preserve_dimension_values: Literal[True] = True
    preserve_dimension_attachments: Literal[True] = True
    preserve_configuration: Literal[True] = True
    preserve_display_state: Literal[True] = True
    preserve_projection_method: Literal[True] = True
    preserve_section_definitions: Literal[True] = True
    preserve_model_associativity: Literal[True] = True
    preserve_frozen_geometry: Literal[True] = True
    allow_delete_objects: Literal[False] = False
    allow_new_manufacturing_annotations: Literal[False] = False
    allow_source_model_write: Literal[False] = False
    allow_upstream_drawing_overwrite: Literal[False] = False
    allow_partial_commit: Literal[False] = False


class SourceInvariants(StrictModel):
    dimension_semantics_sha256: Sha256
    dimension_ids: tuple[StableId, ...] = Field(min_length=1)
    object_snapshot_sha256: Sha256
    object_ids: tuple[StableId, ...] = Field(min_length=1)
    view_names: tuple[str, ...] = Field(min_length=1)
    locked_object_ids: tuple[StableId, ...] = ()
    required_boundary_capabilities: tuple[BoundaryCapability, ...] = Field(
        min_length=1
    )

    @field_validator(
        "dimension_ids",
        "object_ids",
        "view_names",
        "locked_object_ids",
        "required_boundary_capabilities",
        mode="before",
    )
    @classmethod
    def freeze_arrays(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator(
        "dimension_ids",
        "object_ids",
        "view_names",
        "locked_object_ids",
        "required_boundary_capabilities",
    )
    @classmethod
    def unique_arrays(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("source invariant names cannot be empty")
        return _unique(value, "source invariant values")

    @model_validator(mode="after")
    def validate_locked_subset(self) -> "SourceInvariants":
        if not set(self.locked_object_ids).issubset(self.object_ids):
            raise ValueError("locked_object_ids must be a subset of object_ids")
        return self


class SheetFormatAuthorization(StrictModel):
    authorization_id: StableId
    format_id: StableId
    width_m: FiniteFloat = Field(gt=0)
    height_m: FiniteFloat = Field(gt=0)
    approved_by: str = Field(min_length=1, max_length=256)
    approved_at_utc: Rfc3339DateTime
    approval_reference: str = Field(min_length=1, max_length=1000)


class LayoutAuthorization(StrictModel):
    movable_view_names: tuple[str, ...] = ()
    scalable_view_names: tuple[str, ...] = ()
    allow_sheet_scale_change: bool = False
    allowed_sheet_formats: tuple[SheetFormatAuthorization, ...] = ()

    @field_validator(
        "movable_view_names", "scalable_view_names", "allowed_sheet_formats", mode="before"
    )
    @classmethod
    def freeze_arrays(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("movable_view_names", "scalable_view_names")
    @classmethod
    def unique_views(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("authorized view names cannot be empty")
        return _unique(value, "authorized view names")

    @model_validator(mode="after")
    def unique_sheet_authorizations(self) -> "LayoutAuthorization":
        _unique(
            tuple(item.authorization_id for item in self.allowed_sheet_formats),
            "sheet format authorization IDs",
        )
        _unique(
            tuple(item.format_id for item in self.allowed_sheet_formats),
            "sheet format IDs",
        )
        return self


class OperationBase(StrictModel):
    operation_id: StableId
    sequence: int = Field(ge=0)


class MoveDimension(OperationBase):
    kind: Literal["move_dimension"]
    object_id: StableId
    dimension_id: StableId
    target_position_sheet_m: tuple[FiniteFloat, FiniteFloat]
    preserve_attachment: Literal[True] = True

    @field_validator("target_position_sheet_m", mode="before")
    @classmethod
    def freeze_point(cls, value: Any) -> Any:
        return _freeze_json_array(value)


class MoveAnnotation(OperationBase):
    kind: Literal["move_annotation"]
    object_id: StableId
    target_position_sheet_m: tuple[FiniteFloat, FiniteFloat]

    @field_validator("target_position_sheet_m", mode="before")
    @classmethod
    def freeze_point(cls, value: Any) -> Any:
        return _freeze_json_array(value)


class RouteLeader(OperationBase):
    kind: Literal["route_leader"]
    object_id: StableId
    points_sheet_m: tuple[tuple[FiniteFloat, FiniteFloat], ...] = Field(min_length=2)
    preserve_attachment: Literal[True] = True

    @field_validator("points_sheet_m", mode="before")
    @classmethod
    def freeze_points(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(tuple(point) if isinstance(point, list) else point for point in value)
        return value


class MoveView(OperationBase):
    kind: Literal["move_view"]
    view_name: str = Field(min_length=1, max_length=256)
    target_position_sheet_m: tuple[FiniteFloat, FiniteFloat]
    preserve_alignment: Literal[True] = True

    @field_validator("target_position_sheet_m", mode="before")
    @classmethod
    def freeze_point(cls, value: Any) -> Any:
        return _freeze_json_array(value)


class SetDimensionHierarchy(OperationBase):
    kind: Literal["set_dimension_hierarchy"]
    dimension_id: StableId
    tier: Literal["inner", "middle", "outer", "reference"]
    stack_index: int = Field(ge=0)


class SetViewScale(OperationBase):
    kind: Literal["set_view_scale"]
    view_name: str = Field(min_length=1, max_length=256)
    numerator: int = Field(ge=1, le=1000)
    denominator: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def canonical_scale(self) -> "SetViewScale":
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("view scale must be a reduced ratio")
        return self


class SetSheetScale(OperationBase):
    kind: Literal["set_sheet_scale"]
    numerator: int = Field(ge=1, le=1000)
    denominator: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def canonical_scale(self) -> "SetSheetScale":
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("sheet scale must be a reduced ratio")
        return self


class SetSheetFormat(OperationBase):
    kind: Literal["set_sheet_format"]
    authorization_id: StableId
    format_id: StableId
    width_m: FiniteFloat = Field(gt=0)
    height_m: FiniteFloat = Field(gt=0)


LayoutOperation = Annotated[
    MoveDimension
    | MoveAnnotation
    | RouteLeader
    | MoveView
    | SetDimensionHierarchy
    | SetViewScale
    | SetSheetScale
    | SetSheetFormat,
    Field(discriminator="kind"),
]


class DrawingLayoutPlan(StrictModel):
    schema_uri: Literal[
        "https://q3ds.local/contracts/solidworks-drawing-layout-plan-1.0.schema.json"
    ] = Field(alias="$schema")
    protocol_id: Literal["solidworks-drawing-layout-plan"]
    schema_version: Literal["1.0"]
    plan_id: StableId
    created_at_utc: Rfc3339DateTime
    producer: LayoutProducer
    execution_policy: LayoutExecutionPolicy
    handoff: ArtifactBinding
    handoff_id: StableId
    source_dimension_plan: ArtifactBinding
    source_drawing: ArtifactBinding
    dimension_verification_sidecar: ArtifactBinding
    configuration: str = Field(min_length=1, max_length=256)
    source_invariants: SourceInvariants
    authorization: LayoutAuthorization
    operations: tuple[LayoutOperation, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()

    @field_validator("operations", "assumptions", "open_questions", mode="before")
    @classmethod
    def freeze_arrays(cls, value: Any) -> Any:
        return _freeze_json_array(value)

    @field_validator("handoff")
    @classmethod
    def validate_handoff_name(cls, value: ArtifactBinding) -> ArtifactBinding:
        if os.path.basename(value.path).lower() != "drawing-layout-handoff.json":
            raise ValueError("handoff must be named drawing-layout-handoff.json")
        return value

    @field_validator("source_dimension_plan")
    @classmethod
    def validate_dimension_plan_name(cls, value: ArtifactBinding) -> ArtifactBinding:
        if os.path.basename(value.path).lower() != "dimension_plan.json":
            raise ValueError("source_dimension_plan must be named dimension_plan.json")
        return value

    @field_validator("source_drawing")
    @classmethod
    def validate_drawing_suffix(cls, value: ArtifactBinding) -> ArtifactBinding:
        if os.path.splitext(value.path)[1].lower() != ".slddrw":
            raise ValueError("source_drawing path must end with .slddrw")
        return value

    @field_validator("dimension_verification_sidecar")
    @classmethod
    def validate_sidecar_suffix(cls, value: ArtifactBinding) -> ArtifactBinding:
        if os.path.splitext(value.path)[1].lower() != ".json":
            raise ValueError("dimension_verification_sidecar must be JSON")
        return value

    @field_validator("assumptions", "open_questions")
    @classmethod
    def nonempty_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("assumptions and open_questions cannot contain empty text")
        return value

    @model_validator(mode="after")
    def validate_plan_inventory(self) -> "DrawingLayoutPlan":
        paths = (
            self.handoff.path,
            self.source_dimension_plan.path,
            self.source_drawing.path,
            self.dimension_verification_sidecar.path,
        )
        if len({os.path.normcase(path) for path in paths}) != len(paths):
            raise ValueError("DrawingLayoutPlan artifact paths must be distinct")
        if self.open_questions:
            raise ValueError("a frozen DrawingLayoutPlan cannot contain open_questions")
        _unique(tuple(item.operation_id for item in self.operations), "operation IDs")
        sequences = tuple(item.sequence for item in self.operations)
        _unique(sequences, "operation sequences")
        if tuple(sorted(sequences)) != tuple(range(len(sequences))):
            raise ValueError("operation sequences must be contiguous from zero")
        if tuple(item.sequence for item in self.operations) != tuple(range(len(sequences))):
            raise ValueError("operations must be stored in sequence order")
        source = self.source_invariants
        authorization = self.authorization
        if not set(authorization.movable_view_names).issubset(source.view_names):
            raise ValueError("movable views must exist in source_invariants.view_names")
        if not set(authorization.scalable_view_names).issubset(source.view_names):
            raise ValueError("scalable views must exist in source_invariants.view_names")
        sheet_authorizations = {
            item.authorization_id: item for item in authorization.allowed_sheet_formats
        }
        for operation in self.operations:
            if isinstance(operation, (MoveDimension, MoveAnnotation, RouteLeader)):
                if operation.object_id not in source.object_ids:
                    raise ValueError(
                        f"operation {operation.operation_id} references an unknown object"
                    )
                if operation.object_id in source.locked_object_ids:
                    raise ValueError(
                        f"operation {operation.operation_id} targets a locked object"
                    )
            if isinstance(operation, (MoveDimension, SetDimensionHierarchy)) and (
                operation.dimension_id not in source.dimension_ids
            ):
                raise ValueError(
                    f"operation {operation.operation_id} references an unknown dimension"
                )
            if isinstance(operation, MoveView):
                if operation.view_name not in authorization.movable_view_names:
                    raise ValueError("move_view requires explicit view authorization")
            if isinstance(operation, SetViewScale):
                if operation.view_name not in authorization.scalable_view_names:
                    raise ValueError("set_view_scale requires explicit view authorization")
            if isinstance(operation, SetSheetScale) and not (
                authorization.allow_sheet_scale_change
            ):
                raise ValueError("set_sheet_scale requires explicit authorization")
            if isinstance(operation, SetSheetFormat):
                approved = sheet_authorizations.get(operation.authorization_id)
                if approved is None:
                    raise ValueError("set_sheet_format requires an approved authorization_id")
                if (
                    approved.format_id != operation.format_id
                    or approved.width_m != operation.width_m
                    or approved.height_m != operation.height_m
                ):
                    raise ValueError("set_sheet_format must exactly match its authorization")
        return self

    def execution_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.execution_dict(), "DrawingLayoutPlan")


class PublishedDrawingLayoutPlan(StrictModel):
    plan_id: StableId
    path: str = Field(min_length=1)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = _absolute_path(value, "published layout plan path")
        if os.path.basename(normalized).lower() != "drawing_layout_plan.json":
            raise ValueError(
                "published DrawingLayoutPlan must be named drawing_layout_plan.json"
            )
        return normalized


def drawing_layout_plan_from_mapping(candidate: Mapping[str, Any]) -> DrawingLayoutPlan:
    return DrawingLayoutPlan.model_validate(
        json_object_copy(candidate, "DrawingLayoutPlan")
    )


def _absolute_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not os.path.isabs(value):
        raise ValueError(f"{label} must be an absolute path")
    if any(char in value for char in ("*", "?", "[", "]")):
        raise ValueError(f"{label} must not contain wildcard characters")
    return os.path.abspath(value)


def _freeze_json_array(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _unique(value: tuple[Any, ...], label: str) -> tuple[Any, ...]:
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must be unique")
    return value
