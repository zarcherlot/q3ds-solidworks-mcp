"""Strict model-facing contracts for the semantic SolidWorks MCP surface."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    model_validator,
)


_VIEW_PLAN_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "drawing_planner"
    / "contracts"
    / "view-plan.schema.json"
)
_VIEW_PLAN_SCHEMA = json.loads(_VIEW_PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
_DIMENSION_PLAN_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "dimension_planner"
    / "contracts"
    / "dimension-plan.schema.json"
)
_DIMENSION_PLAN_SCHEMA = json.loads(
    _DIMENSION_PLAN_SCHEMA_PATH.read_text(encoding="utf-8")
)


def _qualify_view_plan_refs(value: Any, schema_id: str) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            value["$ref"] = schema_id + reference
        for child in value.values():
            _qualify_view_plan_refs(child, schema_id)
    elif isinstance(value, list):
        for child in value:
            _qualify_view_plan_refs(child, schema_id)


_qualify_view_plan_refs(_VIEW_PLAN_SCHEMA, _VIEW_PLAN_SCHEMA["$id"])
_qualify_view_plan_refs(_DIMENSION_PLAN_SCHEMA, _DIMENSION_PLAN_SCHEMA["$id"])


class ViewPlan(RootModel[dict[str, Any]]):
    """Named wrapper that publishes the exact repository Schema without translating its unions."""

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return copy.deepcopy(_VIEW_PLAN_SCHEMA)


class DimensionPlan(RootModel[dict[str, Any]]):
    """Exact repository DimensionPlan 1.0 contract for the semantic MCP boundary."""

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return copy.deepcopy(_DIMENSION_PLAN_SCHEMA)


ViewId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"),
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApprovedDimensionQuantity(StrictContract):
    kind: Literal["quantity"]
    quantity_kind: Literal["length", "angle", "count"]
    value_si: float


class ApprovedDimensionText(StrictContract):
    kind: Literal["exact_text"]
    text: str = Field(min_length=1)


ApprovedDimensionValue = Annotated[
    Union[ApprovedDimensionQuantity, ApprovedDimensionText], Field(discriminator="kind")
]


class ApprovedDimensionInput(StrictContract):
    input_id: str = Field(
        min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    source_tier: Literal["user_confirmed_input"]
    approved_by: str = Field(min_length=1)
    approved_at_utc: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
    )
    approval_reference: str = Field(min_length=1)
    target_feature_ids: list[str]
    value: ApprovedDimensionValue

    @model_validator(mode="after")
    def validate_feature_ids(self) -> "ApprovedDimensionInput":
        if len(self.target_feature_ids) != len(set(self.target_feature_ids)):
            raise ValueError("target_feature_ids must be unique")
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item)
            for item in self.target_feature_ids
        ):
            raise ValueError("target_feature_ids contain an invalid stable ID")
        return self


class ModelSource(StrictContract):
    path: str = Field(min_length=1, description="Existing absolute .SLDPRT path")
    configuration: str | None = Field(
        default=None,
        min_length=1,
        description="Exact configuration name; omitted means the saved active configuration",
    )
    display_state: str | None = Field(default=None, min_length=1)


class DrawingTarget(StrictContract):
    template_path: str = Field(min_length=1, description="Existing absolute .DRWDOT path")
    output_path: str = Field(min_length=1, description="Absolute .SLDDRW path")
    overwrite: bool = False
    projection: Literal["preserve", "first_angle", "third_angle"] = "preserve"


class SheetContract(StrictContract):
    scale_numerator: float = Field(default=1.0, gt=0)
    scale_denominator: float = Field(default=1.0, gt=0)
    margin_m: float = Field(default=0.005, ge=0, le=0.05)
    view_clearance_m: float = Field(default=0.003, ge=0, le=0.05)
    require_no_overlap: bool = True

    @model_validator(mode="after")
    def validate_scale(self) -> "SheetContract":
        ratio = self.scale_numerator / self.scale_denominator
        if not math.isfinite(ratio) or not 0.001 <= ratio <= 100:
            raise ValueError("sheet scale ratio must be between 0.001 and 100")
        return self


class VerificationContract(StrictContract):
    position_tolerance_m: float = Field(default=0.0005, ge=0.000001, le=0.005)
    scale_tolerance: float = Field(default=0.000001, ge=0.000000001, le=0.001)


class Position(StrictContract):
    x: float = Field(gt=0, le=2, description="Sheet X position in meters")
    y: float = Field(gt=0, le=2, description="Sheet Y position in meters")


class ViewContract(StrictContract):
    id: ViewId
    position: Position
    scale_mode: Literal["sheet", "custom", "parent"]
    scale: float | None = Field(default=None, ge=0.001, le=100)
    display_mode: Literal[
        "wireframe",
        "hidden_lines_removed",
        "hidden_lines_visible",
        "shaded",
        "shaded_with_edges",
    ] = "hidden_lines_removed"
    tangent_edges: Literal["removed", "fonted", "visible"] = "removed"
    configuration: str | None = Field(default=None, min_length=1)
    display_state: str | None = Field(default=None, min_length=1)
    lock_position: bool = True

    @model_validator(mode="after")
    def validate_custom_scale(self) -> "ViewContract":
        if self.scale_mode == "custom" and self.scale is None:
            raise ValueError("scale is required when scale_mode='custom'")
        if self.scale_mode != "custom" and self.scale is not None:
            raise ValueError("scale must be omitted unless scale_mode='custom'")
        return self


class BaseView(ViewContract):
    kind: Literal["base"]
    orientation: Literal["front", "back", "left", "right", "top", "bottom", "isometric"]
    scale_mode: Literal["sheet", "custom"] = "sheet"


class ProjectedView(ViewContract):
    kind: Literal["projected"]
    parent_id: ViewId
    scale_mode: Literal["sheet", "custom", "parent"] = "parent"

    @model_validator(mode="after")
    def validate_inheritance(self) -> "ProjectedView":
        if self.configuration is not None or self.display_state is not None:
            raise ValueError(
                "projected views inherit configuration/display_state from their parent"
            )
        return self


PlannedView = Annotated[Union[BaseView, ProjectedView], Field(discriminator="kind")]


class DrawingPlan(StrictContract):
    schema_version: Literal["1.0"]
    model: ModelSource
    drawing: DrawingTarget
    sheet: SheetContract
    views: list[PlannedView] = Field(min_length=1, max_length=16)
    verification: VerificationContract = Field(default_factory=VerificationContract)

    @model_validator(mode="after")
    def validate_plan(self) -> "DrawingPlan":
        source = _absolute_existing(self.model.path, ".sldprt", "model.path")
        template = _absolute_existing(
            self.drawing.template_path, ".drwdot", "drawing.template_path"
        )
        output = _absolute(self.drawing.output_path, ".slddrw", "drawing.output_path")
        parent = os.path.dirname(output)
        if not os.path.isdir(parent):
            raise ValueError(f"drawing output directory does not exist: {parent}")
        if os.path.normcase(output) in {os.path.normcase(source), os.path.normcase(template)}:
            raise ValueError("drawing.output_path must differ from all input paths")

        seen: dict[str, PlannedView] = {}
        tolerance = self.verification.position_tolerance_m
        for view in self.views:
            if view.id in seen:
                raise ValueError(f"duplicate view id: {view.id}")
            if isinstance(view, ProjectedView):
                parent_view = seen.get(view.parent_id)
                if parent_view is None:
                    raise ValueError(
                        f"projected view '{view.id}' must reference a preceding parent_id"
                    )
                dx = abs(view.position.x - parent_view.position.x)
                dy = abs(view.position.y - parent_view.position.y)
                if (dx <= tolerance and dy <= tolerance) or (
                    dx > tolerance and dy > tolerance
                ):
                    raise ValueError(
                        f"projected view '{view.id}' must align horizontally or vertically "
                        "with its parent"
                    )
            seen[view.id] = view
        return self

    def execution_dict(self) -> dict:
        """Return JSON-compatible data with defaults and no null optionals."""
        return self.model_dump(mode="json", exclude_none=True)

    def canonical_json(self) -> str:
        return json.dumps(
            self.execution_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def parse_drawing_plan(plan_json: str) -> DrawingPlan:
    if not isinstance(plan_json, str) or not plan_json.strip():
        raise ValueError("plan_json must be a non-empty JSON object string")
    return DrawingPlan.model_validate_json(plan_json)


def validate_model_path(model_path: str) -> str:
    return _absolute_existing(model_path, ".sldprt", "model_path")


def validate_drawing_template_path(template_path: str) -> str:
    return _absolute_existing(
        template_path, ".drwdot", "drawing_template_path"
    )


def validate_publication_directory(directory: str) -> str:
    if not isinstance(directory, str) or not directory.strip() or not os.path.isabs(directory):
        raise ValueError("publication_directory must be an absolute existing directory")
    if any(character in directory for character in ("*", "?", "[", "]")):
        raise ValueError("publication_directory must not contain wildcard characters")
    full = os.path.abspath(directory)
    if not os.path.isdir(full):
        raise ValueError(f"publication_directory does not exist: {full}")
    return full


def validate_host_report_directory(directory: str) -> str:
    """Validate the only caller-controlled write location accepted by HostBootstrap."""
    full = validate_publication_directory(directory)
    path = Path(full)
    if path == Path(path.anchor):
        raise ValueError("output_directory must not be a filesystem root")
    if any(character in directory for character in ('"', "\r", "\n")):
        raise ValueError("output_directory contains unsupported characters")
    return full


def _absolute_existing(path: str, extension: str, label: str) -> str:
    full = _absolute(path, extension, label)
    if not os.path.isfile(full):
        raise ValueError(f"{label} does not exist: {full}")
    return full


def _absolute(path: str, extension: str, label: str) -> str:
    if not isinstance(path, str) or not path.strip() or not os.path.isabs(path):
        raise ValueError(f"{label} must be an absolute {extension.upper()} path")
    full = os.path.abspath(path)
    if os.path.splitext(full)[1].lower() != extension:
        raise ValueError(f"{label} must end with {extension}")
    return full
