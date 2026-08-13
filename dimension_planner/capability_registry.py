"""Versioned execution-capability assessment for DimensionPlan 1.0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .f0_evidence import F0_CAPABILITY_IDS
from .planning_models import SemanticVersion


_DIMENSION_TYPES = {
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
}
_ELEMENTS = {
    "model_dimension_import",
    "attachment_persistent_reference",
    "annotation_position",
    "annotation_text_bounds",
    "dimension_tolerance",
    "dimension_prefix_suffix",
    "save_reopen_stable_identity",
}
CapabilityName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
]
CapabilityStatus = Literal["supported", "planned", "unsupported"]


class DimensionExecutionCapabilityError(ValueError):
    """Raised when native creation is requested for a blocked DimensionPlan."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CapabilityEntry(_StrictModel):
    status: CapabilityStatus
    reason: str = Field(min_length=1, max_length=1000)
    verification: Literal["none", "unit", "live"]
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_evidence(self) -> "CapabilityEntry":
        if self.status in {"supported", "unsupported"}:
            if self.verification != "live" or self.evidence_sha256 is None:
                raise ValueError(
                    "supported/unsupported capabilities require bound live evidence"
                )
        elif self.evidence_sha256 is not None:
            raise ValueError("planned capabilities cannot claim live evidence")
        return self


class ProbeCapabilityEntry(_StrictModel):
    id: CapabilityName
    status: CapabilityStatus


class LiveEvidenceBinding(_StrictModel):
    summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    solidworks_revision: Literal["33.5.0"]


class DimensionCapabilityManifest(_StrictModel):
    protocol_id: Literal["solidworks-dimension-executor-capabilities"]
    schema_version: Literal["1.0"]
    registry_version: SemanticVersion
    executor: str = Field(min_length=1, max_length=128)
    executor_version: SemanticVersion
    plan_protocol_id: Literal["solidworks-dimension-plan"]
    plan_schema_version: Literal["1.0"]
    solidworks_target: Literal["2025 SP5"]
    solidworks_revision: Literal["33.5.0"]
    promotion_policy: str = Field(min_length=1, max_length=2000)
    live_evidence: LiveEvidenceBinding | None
    capabilities: tuple[ProbeCapabilityEntry, ...]
    dimension_types: dict[CapabilityName, CapabilityEntry]
    elements: dict[CapabilityName, CapabilityEntry]

    @field_validator("capabilities", mode="before")
    @classmethod
    def freeze_capabilities(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_catalogs(self) -> "DimensionCapabilityManifest":
        if tuple(item.id for item in self.capabilities) != F0_CAPABILITY_IDS:
            raise ValueError("capabilities must preserve the frozen F0 catalog order")
        if set(self.dimension_types) != _DIMENSION_TYPES:
            raise ValueError("dimension_types must enumerate the DimensionPlan 1.0 union")
        if set(self.elements) != _ELEMENTS:
            raise ValueError("elements must enumerate all shared dimension capabilities")
        if any(
            entry.status in {"supported", "unsupported"}
            for entry in (*self.dimension_types.values(), *self.elements.values())
        ) and self.live_evidence is None:
            raise ValueError("final capability conclusions require live_evidence")
        if self.live_evidence is not None:
            final_entries = (
                entry
                for entry in (*self.dimension_types.values(), *self.elements.values())
                if entry.status in {"supported", "unsupported"}
            )
            if any(
                entry.evidence_sha256 != self.live_evidence.summary_sha256
                for entry in final_entries
            ):
                raise ValueError(
                    "final capability evidence must match live_evidence.summary_sha256"
                )
        return self


class DimensionCapabilityAssessment(_StrictModel):
    status: Literal["supported", "capability_blocked"]
    manifest_version: SemanticVersion
    unsupported_capabilities: tuple[str, ...] = ()

    @field_validator("unsupported_capabilities", mode="before")
    @classmethod
    def freeze_unsupported_capabilities(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class DimensionCapabilityRegistry:
    def __init__(self, manifest: DimensionCapabilityManifest):
        self.manifest = manifest

    @classmethod
    def from_path(cls, path: Path) -> "DimensionCapabilityRegistry":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(DimensionCapabilityManifest.model_validate(payload))

    def assess(self, plan: Mapping[str, object]) -> DimensionCapabilityAssessment:
        if plan.get("protocol_id") != self.manifest.plan_protocol_id:
            raise ValueError("plan protocol_id does not match the dimension manifest")
        if plan.get("schema_version") != self.manifest.plan_schema_version:
            raise ValueError("plan schema_version does not match the dimension manifest")
        dimensions = plan.get("dimensions")
        if not isinstance(dimensions, (list, tuple)) or not dimensions:
            raise ValueError("plan dimensions must be a non-empty array")

        blocked: set[str] = set()
        for index, dimension in enumerate(dimensions):
            if not isinstance(dimension, Mapping):
                raise ValueError(f"dimensions[{index}] must be an object")
            kind = dimension.get("kind")
            if kind not in self.manifest.dimension_types:
                raise ValueError(f"dimensions[{index}].kind is unknown: {kind!r}")
            self._assess("dimension_type", str(kind), blocked)
            for element in (
                "attachment_persistent_reference",
                "annotation_position",
                "save_reopen_stable_identity",
            ):
                self._assess("element", element, blocked)
            source = dimension.get("source")
            if isinstance(source, Mapping) and source.get("source_tier") == "model_or_pmi":
                self._assess("element", "model_dimension_import", blocked)
            if dimension.get("tolerance") is not None:
                self._assess("element", "dimension_tolerance", blocked)
            display = dimension.get("display_format")
            if isinstance(display, Mapping) and (
                display.get("prefix") or display.get("suffix")
            ):
                self._assess("element", "dimension_prefix_suffix", blocked)

        unsupported = tuple(sorted(blocked))
        return DimensionCapabilityAssessment(
            status="capability_blocked" if unsupported else "supported",
            manifest_version=self.manifest.registry_version,
            unsupported_capabilities=unsupported,
        )

    def require_supported(self, plan: Mapping[str, object]) -> None:
        assessment = self.assess(plan)
        if assessment.status != "supported":
            raise DimensionExecutionCapabilityError(
                "DimensionPlan execution is capability_blocked: "
                + ", ".join(assessment.unsupported_capabilities)
            )

    def _assess(self, namespace: str, name: str, blocked: set[str]) -> None:
        collection = (
            self.manifest.dimension_types
            if namespace == "dimension_type"
            else self.manifest.elements
        )
        if collection[name].status != "supported":
            blocked.add(f"{namespace}.{name}")


def current_registry() -> DimensionCapabilityRegistry:
    return DimensionCapabilityRegistry.from_path(
        Path(__file__).resolve().parent / "capabilities" / "current.json"
    )
