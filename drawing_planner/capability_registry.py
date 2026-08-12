"""Versioned execution-capability registry for repository C# ViewPlan support."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


_ALL_VIEW_TYPES = {
    "model_view",
    "projected_view",
    "full_section",
    "half_section",
    "offset_section",
    "aligned_section",
    "broken_out_section",
    "removed_section",
    "detail_view",
    "auxiliary_view",
}
_ALL_ELEMENTS = {"center_marks", "symmetry_centerlines", "view_labels"}
CapabilityStatus = Literal["supported", "planned", "unsupported"]
CapabilityName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CapabilityEntry(_StrictModel):
    status: CapabilityStatus
    reason: str = Field(min_length=1, max_length=1000)
    verification: Literal["none", "unit", "live"]

    @model_validator(mode="after")
    def supported_requires_live_verification(self) -> "CapabilityEntry":
        if self.status == "supported" and self.verification != "live":
            raise ValueError("supported capabilities require live verification")
        return self


class CapabilityManifest(_StrictModel):
    protocol_id: Literal["q3ds-solidworks-view-plan-capabilities"]
    schema_version: Literal["1.0"]
    executor: str = Field(min_length=1, max_length=128)
    executor_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    plan_protocol_id: Literal["solidworks-view-plan"]
    plan_schema_version: Literal["1.4"]
    view_types: dict[CapabilityName, CapabilityEntry]
    elements: dict[CapabilityName, CapabilityEntry]

    @model_validator(mode="after")
    def require_complete_namespaces(self) -> "CapabilityManifest":
        if set(self.view_types) != _ALL_VIEW_TYPES:
            raise ValueError("view_types must enumerate the complete schema-1.4 view union")
        if set(self.elements) != _ALL_ELEMENTS:
            raise ValueError("elements must enumerate all separately assessed elements")
        return self


class CapabilityAssessment(_StrictModel):
    status: Literal["supported", "capability_blocked"]
    manifest_version: str
    unsupported_capabilities: tuple[str, ...] = ()


class CapabilityRegistry:
    def __init__(self, manifest: CapabilityManifest):
        self.manifest = manifest

    @classmethod
    def from_path(cls, path: Path) -> "CapabilityRegistry":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(CapabilityManifest.model_validate(payload))

    def assess(self, plan: Mapping[str, Any]) -> CapabilityAssessment:
        if plan.get("protocol_id") != self.manifest.plan_protocol_id:
            raise ValueError("plan protocol_id does not match the capability manifest")
        if plan.get("schema_version") != self.manifest.plan_schema_version:
            raise ValueError("plan schema_version does not match the capability manifest")
        views = plan.get("views")
        if not isinstance(views, list) or not views:
            raise ValueError("plan views must be a non-empty array")

        blocked: set[str] = set()
        for index, view in enumerate(views):
            if not isinstance(view, Mapping):
                raise ValueError(f"views[{index}] must be an object")
            view_type = view.get("type")
            if view_type not in self.manifest.view_types:
                raise ValueError(f"views[{index}].type is unknown: {view_type!r}")
            if self.manifest.view_types[view_type].status != "supported":
                blocked.add(f"view_type.{view_type}")
            if view_type == "auxiliary_view":
                definition = view.get("auxiliary_definition")
                if isinstance(definition, Mapping) and definition.get("show_arrow") is False:
                    blocked.add("view_type.auxiliary_view.hidden_arrow")
            if view.get("center_marks"):
                self._assess_element("center_marks", blocked)
            if view.get("symmetry_centerlines"):
                self._assess_element("symmetry_centerlines", blocked)
            label = view.get("label")
            if isinstance(label, Mapping) and label.get("show") is True:
                self._assess_element("view_labels", blocked)

        unsupported = tuple(sorted(blocked))
        return CapabilityAssessment(
            status="capability_blocked" if unsupported else "supported",
            manifest_version=self.manifest.executor_version,
            unsupported_capabilities=unsupported,
        )

    def _assess_element(self, name: str, blocked: set[str]) -> None:
        if self.manifest.elements[name].status != "supported":
            blocked.add(f"element.{name}")


def current_registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_path(
        Path(__file__).resolve().parent / "capabilities" / "current.json"
    )
