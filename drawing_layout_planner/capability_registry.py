"""Versioned capability assessment for DrawingLayoutPlan 1.0."""

from __future__ import annotations

import hashlib
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

from .g0_evidence import CAPABILITY_PATH, G0_CAPABILITY_IDS, load_g0_capability_manifest
from .planning_models import DrawingLayoutPlan, SemanticVersion


LAYOUT_OPERATION_IDS = (
    "move_dimension",
    "move_annotation",
    "route_leader",
    "move_view",
    "set_dimension_hierarchy",
    "set_view_scale",
    "set_sheet_scale",
    "set_sheet_format",
)
LAYOUT_SAFETY_IDS = (
    "dimension_semantic_preservation",
    "view_semantic_preservation",
    "object_identity_preservation",
    "collision_readback",
    "save_reopen_layout_fingerprint",
    "authorized_sheet_change",
)
CapabilityName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
]
CapabilityStatus = Literal["supported", "planned", "unsupported"]


class DrawingLayoutExecutionCapabilityError(ValueError):
    """Raised when execution is requested for a blocked layout plan."""


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


class BoundaryRegistryBinding(_StrictModel):
    protocol_id: Literal["solidworks-drawing-layout-executor-capabilities"]
    registry_version: SemanticVersion
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DrawingLayoutCapabilityManifest(_StrictModel):
    protocol_id: Literal["solidworks-drawing-layout-plan-capabilities"]
    schema_version: Literal["1.0"]
    registry_version: SemanticVersion
    executor: str = Field(min_length=1, max_length=128)
    executor_version: SemanticVersion
    plan_protocol_id: Literal["solidworks-drawing-layout-plan"]
    plan_schema_version: Literal["1.0"]
    solidworks_target: Literal["2025 SP5"]
    solidworks_revision: Literal["33.5.0"]
    promotion_policy: str = Field(min_length=1, max_length=2000)
    boundary_registry: BoundaryRegistryBinding
    operations: dict[CapabilityName, CapabilityEntry]
    safety_elements: dict[CapabilityName, CapabilityEntry]

    @model_validator(mode="after")
    def validate_catalogs(self) -> "DrawingLayoutCapabilityManifest":
        if tuple(self.operations) != LAYOUT_OPERATION_IDS:
            raise ValueError("operations must preserve the frozen G2 catalog and order")
        if tuple(self.safety_elements) != LAYOUT_SAFETY_IDS:
            raise ValueError("safety_elements must preserve the frozen G2 catalog and order")
        return self


class DrawingLayoutCapabilityAssessment(_StrictModel):
    status: Literal["supported", "capability_blocked"]
    manifest_version: SemanticVersion
    unsupported_capabilities: tuple[str, ...] = ()

    @field_validator("unsupported_capabilities", mode="before")
    @classmethod
    def freeze_blockers(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class DrawingLayoutCapabilityRegistry:
    def __init__(
        self,
        manifest: DrawingLayoutCapabilityManifest,
        *,
        boundary_manifest: Mapping[str, object],
        boundary_manifest_sha256: str,
    ):
        self.manifest = manifest
        self.boundary_manifest = boundary_manifest
        self.boundary_manifest_sha256 = boundary_manifest_sha256
        self._validate_boundary_binding()

    @classmethod
    def from_paths(
        cls,
        manifest_path: Path,
        boundary_manifest_path: Path = CAPABILITY_PATH,
    ) -> "DrawingLayoutCapabilityRegistry":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        boundary = load_g0_capability_manifest(boundary_manifest_path)
        boundary_sha256 = hashlib.sha256(boundary_manifest_path.read_bytes()).hexdigest()
        return cls(
            DrawingLayoutCapabilityManifest.model_validate(payload),
            boundary_manifest=boundary,
            boundary_manifest_sha256=boundary_sha256,
        )

    def _validate_boundary_binding(self) -> None:
        binding = self.manifest.boundary_registry
        if self.boundary_manifest.get("protocol_id") != binding.protocol_id:
            raise ValueError("G2 manifest boundary protocol binding does not match G0")
        if self.boundary_manifest.get("registry_version") != binding.registry_version:
            raise ValueError("G2 manifest boundary registry version does not match G0")
        if self.boundary_manifest_sha256 != binding.manifest_sha256:
            raise ValueError("G2 manifest boundary SHA-256 binding does not match G0")

    def assess(
        self, plan: DrawingLayoutPlan | Mapping[str, object]
    ) -> DrawingLayoutCapabilityAssessment:
        normalized = (
            plan
            if isinstance(plan, DrawingLayoutPlan)
            else DrawingLayoutPlan.model_validate(plan)
        )
        if normalized.protocol_id != self.manifest.plan_protocol_id:
            raise ValueError("plan protocol_id does not match the layout manifest")
        if normalized.schema_version != self.manifest.plan_schema_version:
            raise ValueError("plan schema_version does not match the layout manifest")

        blocked: set[str] = set()
        for operation in normalized.operations:
            entry = self.manifest.operations.get(operation.kind)
            if entry is None:
                raise ValueError(f"unknown layout operation capability: {operation.kind}")
            if entry.status != "supported":
                blocked.add(f"operation.{operation.kind}")
        for name, entry in self.manifest.safety_elements.items():
            if entry.status != "supported":
                blocked.add(f"safety.{name}")

        boundary_rows = self.boundary_manifest.get("capabilities")
        if not isinstance(boundary_rows, list):
            raise ValueError("bound G0 manifest lacks a capability catalog")
        boundary_status = {
            str(row["id"]): str(row["status"])
            for row in boundary_rows
            if isinstance(row, Mapping) and "id" in row and "status" in row
        }
        if tuple(boundary_status) != G0_CAPABILITY_IDS:
            raise ValueError("bound G0 capability catalog/order has changed")
        for name in normalized.source_invariants.required_boundary_capabilities:
            if boundary_status[name] != "supported":
                blocked.add(f"boundary.{name}")

        unsupported = tuple(sorted(blocked))
        return DrawingLayoutCapabilityAssessment(
            status="capability_blocked" if unsupported else "supported",
            manifest_version=self.manifest.registry_version,
            unsupported_capabilities=unsupported,
        )

    def require_supported(
        self, plan: DrawingLayoutPlan | Mapping[str, object]
    ) -> None:
        assessment = self.assess(plan)
        if assessment.status != "supported":
            raise DrawingLayoutExecutionCapabilityError(
                "DrawingLayoutPlan execution is capability_blocked: "
                + ", ".join(assessment.unsupported_capabilities)
            )

    def require_qualification_eligible(
        self, plan: DrawingLayoutPlan | Mapping[str, object]
    ) -> None:
        """Allow planned implementation evidence, but never known-unsupported boundaries."""
        normalized = (
            plan
            if isinstance(plan, DrawingLayoutPlan)
            else DrawingLayoutPlan.model_validate(plan)
        )
        blocked: list[str] = []
        for operation in normalized.operations:
            entry = self.manifest.operations.get(operation.kind)
            if entry is None or entry.status == "unsupported":
                blocked.append(f"operation.{operation.kind}")
        for name, entry in self.manifest.safety_elements.items():
            if entry.status == "unsupported":
                blocked.append(f"safety.{name}")
        boundary_rows = self.boundary_manifest.get("capabilities")
        if not isinstance(boundary_rows, list):
            raise ValueError("bound G0 manifest lacks a capability catalog")
        boundary_status = {
            str(row["id"]): str(row["status"])
            for row in boundary_rows
            if isinstance(row, Mapping) and "id" in row and "status" in row
        }
        for name in normalized.source_invariants.required_boundary_capabilities:
            if boundary_status.get(name) != "supported":
                blocked.append(f"boundary.{name}")
        if blocked:
            raise DrawingLayoutExecutionCapabilityError(
                "DrawingLayoutPlan G7 qualification is blocked by known-unsupported or "
                "non-exact capability: " + ", ".join(sorted(set(blocked)))
            )


def current_registry() -> DrawingLayoutCapabilityRegistry:
    package_root = Path(__file__).resolve().parent
    return DrawingLayoutCapabilityRegistry.from_paths(
        package_root / "capabilities" / "plan-current.json",
        package_root / "capabilities" / "current.json",
    )
