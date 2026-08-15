"""Immutable request and result models for the deterministic G3 layout engine."""

from __future__ import annotations

import math
import os
from typing import Any, Literal

from pydantic import Field, FiniteFloat, field_validator, model_validator

from dimension_planner.planning_models import DimensionPlanningRequest

from .planning_models import (
    ArtifactBinding,
    LayoutAuthorization,
    PublishedDrawingLayoutPlan,
    Rfc3339DateTime,
    Sha256,
    StableId,
    StrictModel,
)


GateStatus = Literal["pass", "fail", "not_run"]


class ScaleRatio(StrictModel):
    numerator: int = Field(ge=1, le=1000)
    denominator: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def reduced(self) -> "ScaleRatio":
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("scale candidates must use reduced ratios")
        return self

    @property
    def value(self) -> float:
        return self.numerator / self.denominator


class DimensionLayoutIntent(StrictModel):
    dimension_id: StableId
    object_id: StableId
    preferred_position_sheet_m: tuple[FiniteFloat, FiniteFloat]
    tier: Literal["inner", "middle", "outer", "reference"]
    stack_index: int = Field(ge=0)
    priority: int = Field(ge=0, le=1000)

    @field_validator("preferred_position_sheet_m", mode="before")
    @classmethod
    def freeze_point(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class AnnotationLayoutIntent(StrictModel):
    object_id: StableId
    preferred_position_sheet_m: tuple[FiniteFloat, FiniteFloat]
    priority: int = Field(ge=0, le=1000)

    @field_validator("preferred_position_sheet_m", mode="before")
    @classmethod
    def freeze_point(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class LeaderLayoutIntent(StrictModel):
    object_id: StableId
    attachment_point_sheet_m: tuple[FiniteFloat, FiniteFloat]
    preferred_end_sheet_m: tuple[FiniteFloat, FiniteFloat]
    priority: int = Field(ge=0, le=1000)

    @field_validator(
        "attachment_point_sheet_m", "preferred_end_sheet_m", mode="before"
    )
    @classmethod
    def freeze_points(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class ViewLayoutIntent(StrictModel):
    view_name: str = Field(min_length=1, max_length=256)
    preferred_position_sheet_m: tuple[FiniteFloat, FiniteFloat]
    priority: int = Field(ge=0, le=1000)

    @field_validator("preferred_position_sheet_m", mode="before")
    @classmethod
    def freeze_point(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class ViewScaleIntent(StrictModel):
    view_name: str = Field(min_length=1, max_length=256)
    candidates: tuple[ScaleRatio, ...] = Field(min_length=1)
    priority: int = Field(ge=0, le=1000)

    @field_validator("candidates", mode="before")
    @classmethod
    def freeze_candidates(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class SheetScaleIntent(StrictModel):
    candidates: tuple[ScaleRatio, ...] = Field(min_length=1)
    priority: int = Field(ge=0, le=1000)

    @field_validator("candidates", mode="before")
    @classmethod
    def freeze_candidates(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class SheetFormatIntent(StrictModel):
    authorization_ids: tuple[StableId, ...] = Field(min_length=1)
    priority: int = Field(ge=0, le=1000)

    @field_validator("authorization_ids", mode="before")
    @classmethod
    def freeze_authorizations(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("authorization_ids")
    @classmethod
    def unique_authorizations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("sheet format authorization_ids must be unique")
        return value


class LayoutIntents(StrictModel):
    dimensions: tuple[DimensionLayoutIntent, ...] = ()
    annotations: tuple[AnnotationLayoutIntent, ...] = ()
    leaders: tuple[LeaderLayoutIntent, ...] = ()
    views: tuple[ViewLayoutIntent, ...] = ()
    view_scales: tuple[ViewScaleIntent, ...] = ()
    sheet_scale: SheetScaleIntent | None = None
    sheet_format: SheetFormatIntent | None = None

    @field_validator(
        "dimensions", "annotations", "leaders", "views", "view_scales", mode="before"
    )
    @classmethod
    def freeze_arrays(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_inventory(self) -> "LayoutIntents":
        if not any(
            (
                self.dimensions,
                self.annotations,
                self.leaders,
                self.views,
                self.view_scales,
                self.sheet_scale,
                self.sheet_format,
            )
        ):
            raise ValueError("at least one layout intent is required")
        for label, values in (
            ("dimension IDs", tuple(item.dimension_id for item in self.dimensions)),
            ("dimension object IDs", tuple(item.object_id for item in self.dimensions)),
            ("annotation object IDs", tuple(item.object_id for item in self.annotations)),
            ("leader object IDs", tuple(item.object_id for item in self.leaders)),
            ("view names", tuple(item.view_name for item in self.views)),
            ("view scale names", tuple(item.view_name for item in self.view_scales)),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"layout intent {label} must be unique")
        return self


class LayoutPlanningRequest(StrictModel):
    protocol_id: Literal["solidworks-drawing-layout-planning-request"]
    schema_version: Literal["1.0"]
    request_id: StableId
    plan_id: StableId
    created_at_utc: Rfc3339DateTime
    source_dimension_request: DimensionPlanningRequest
    handoff: ArtifactBinding
    publication_directory: str = Field(min_length=1)
    authorization: LayoutAuthorization
    intents: LayoutIntents
    assumptions: tuple[str, ...] = ()

    @field_validator("assumptions", mode="before")
    @classmethod
    def freeze_assumptions(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("handoff")
    @classmethod
    def handoff_filename(cls, value: ArtifactBinding) -> ArtifactBinding:
        if os.path.basename(value.path).lower() != "drawing-layout-handoff.json":
            raise ValueError("handoff must be named drawing-layout-handoff.json")
        return value

    @field_validator("publication_directory")
    @classmethod
    def absolute_publication_directory(cls, value: str) -> str:
        if not os.path.isabs(value) or any(char in value for char in "*?[]"):
            raise ValueError("publication_directory must be an absolute path")
        return os.path.abspath(value)

    @field_validator("assumptions")
    @classmethod
    def nonempty_assumptions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("assumptions cannot contain empty text")
        return value


class LayoutValidationIssue(StrictModel):
    gate: Literal[
        "integrity",
        "phase_order",
        "safe_area",
        "locked_zones",
        "collisions",
        "dimension_crossing",
        "projection_alignment",
        "minimum_spacing",
        "readability",
        "solver",
    ]
    code: StableId
    message: str = Field(min_length=1, max_length=2000)
    references: tuple[str, ...] = ()

    @field_validator("references", mode="before")
    @classmethod
    def freeze_references(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class LayoutPlanningValidation(StrictModel):
    integrity: GateStatus
    phase_order: GateStatus
    safe_area: GateStatus
    locked_zones: GateStatus
    collisions: GateStatus
    dimension_crossing: GateStatus
    projection_alignment: GateStatus
    minimum_spacing: GateStatus
    readability: GateStatus
    solver: GateStatus
    issues: tuple[LayoutValidationIssue, ...] = ()

    @field_validator("issues", mode="before")
    @classmethod
    def freeze_issues(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @property
    def passed(self) -> bool:
        return all(
            getattr(self, field) == "pass"
            for field in (
                "integrity",
                "phase_order",
                "safe_area",
                "locked_zones",
                "collisions",
                "dimension_crossing",
                "projection_alignment",
                "minimum_spacing",
                "readability",
                "solver",
            )
        )


class LayoutPlanningAudit(StrictModel):
    request_sha256: Sha256
    handoff_sha256: Sha256
    ruleset_sha256: Sha256
    candidate_sha256: Sha256 | None = None
    capability_manifest_version: str | None = None


class LayoutPlanningResult(StrictModel):
    status: Literal["published", "rejected"]
    execution_readiness: Literal[
        "supported", "capability_blocked", "not_assessed"
    ]
    validation: LayoutPlanningValidation
    plan: PublishedDrawingLayoutPlan | None
    audit: LayoutPlanningAudit
    unsupported_capabilities: tuple[str, ...] = ()

    @field_validator("unsupported_capabilities", mode="before")
    @classmethod
    def freeze_capabilities(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_status(self) -> "LayoutPlanningResult":
        if self.status == "published" and self.plan is None:
            raise ValueError("published results require a plan")
        if self.status == "rejected" and self.plan is not None:
            raise ValueError("rejected results cannot expose a plan")
        if self.status == "rejected" and self.execution_readiness != "not_assessed":
            raise ValueError("rejected results cannot claim execution readiness")
        return self
