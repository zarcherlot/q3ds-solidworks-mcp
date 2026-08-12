"""Strict, read-only access to immutable mechanical semantic-feature taxonomies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


RequirementKind = Literal[
    "opening_and_count",
    "shape_true_form",
    "internal_profile",
    "depth_extent",
    "axis_direction",
    "angular_orientation",
    "location_relation",
    "pattern_relation",
    "transition_detail",
]
DerivationKind = Literal[
    "deterministic_geometry",
    "model_feature_data",
    "model_pmi",
    "controlled_user_requirement",
    "planner_engineering_decision",
]
FeatureKind = Literal[
    "global_shape",
    "geometric_feature",
    "specialized_structure",
    "relation",
    "manufacturing_context",
]
EvidenceNeed = Literal[
    "bounding_box",
    "brep_face_group",
    "brep_edge_group",
    "feature_tree_data",
    "axis",
    "normal",
    "opening",
    "axial_extent",
    "occurrences",
    "relation_geometry",
    "model_pmi",
    "controlled_requirement",
]
FeatureCode = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", max_length=128
    ),
]

_ALL_REQUIREMENT_KINDS = frozenset(RequirementKind.__args__)
_ALL_DERIVATION_KINDS = frozenset(DerivationKind.__args__)
_ALL_FEATURE_KINDS = frozenset(FeatureKind.__args__)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def freeze_json_arrays(cls, value):
        """Normalize JSON arrays for immutable tuple fields without coercing scalar types."""

        return tuple(value) if isinstance(value, list) else value


class FeatureLabels(_StrictModel):
    zh_cn: str = Field(min_length=1, max_length=128)
    en: str = Field(min_length=1, max_length=128)


class FeatureScope(_StrictModel):
    includes: tuple[str, ...] = Field(min_length=1)
    excludes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique_non_empty_entries(self) -> "FeatureScope":
        for label, values in (("includes", self.includes), ("excludes", self.excludes)):
            if len(values) != len(set(values)):
                raise ValueError(f"scope.{label} must not contain duplicates")
            if any(not value.strip() for value in values):
                raise ValueError(f"scope.{label} entries must not be blank")
        return self


class MechanicalFeatureClass(_StrictModel):
    code: FeatureCode
    kind: FeatureKind
    parent_code: FeatureCode | None
    abstract: bool
    labels: FeatureLabels
    definition: str = Field(min_length=1, max_length=1000)
    scope: FeatureScope
    default_requirements: tuple[RequirementKind, ...] = ()
    evidence_needs: tuple[EvidenceNeed, ...] = ()
    source_status: Literal["pending", "referenced", "verified"]
    source_refs: tuple[str, ...] = ()
    implementation_status: Literal[
        "vocabulary_only",
        "initializer_planned",
        "deterministic_extraction",
        "live_verified",
    ]

    @model_validator(mode="after")
    def validate_local_consistency(self) -> "MechanicalFeatureClass":
        if len(self.default_requirements) != len(set(self.default_requirements)):
            raise ValueError("default_requirements must not contain duplicates")
        if len(self.evidence_needs) != len(set(self.evidence_needs)):
            raise ValueError("evidence_needs must not contain duplicates")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("source_refs must not contain duplicates")
        if self.source_status == "pending" and self.source_refs:
            raise ValueError("pending source status cannot claim source_refs")
        if self.source_status != "pending" and not self.source_refs:
            raise ValueError("referenced or verified source status requires source_refs")
        if self.implementation_status == "live_verified" and self.source_status != "verified":
            raise ValueError("live_verified classes require verified source provenance")
        return self


class MechanicalFeatureTaxonomy(_StrictModel):
    protocol_id: Literal["q3ds-solidworks-mechanical-feature-taxonomy"]
    schema_version: Literal["1.0"]
    taxonomy_id: Literal["mechanical-features"]
    taxonomy_version: str = Field(
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[a-z0-9.-]+)?$"
    )
    status: Literal["experimental", "released", "retired"]
    requirement_kinds: tuple[RequirementKind, ...]
    derivation_kinds: tuple[DerivationKind, ...]
    feature_classes: tuple[MechanicalFeatureClass, ...] = Field(min_length=5)

    @model_validator(mode="after")
    def validate_registry(self) -> "MechanicalFeatureTaxonomy":
        if set(self.requirement_kinds) != _ALL_REQUIREMENT_KINDS:
            raise ValueError("requirement_kinds must enumerate the complete namespace")
        if len(self.requirement_kinds) != len(_ALL_REQUIREMENT_KINDS):
            raise ValueError("requirement_kinds must not contain duplicates")
        if set(self.derivation_kinds) != _ALL_DERIVATION_KINDS:
            raise ValueError("derivation_kinds must enumerate the complete namespace")
        if len(self.derivation_kinds) != len(_ALL_DERIVATION_KINDS):
            raise ValueError("derivation_kinds must not contain duplicates")

        by_code = {entry.code: entry for entry in self.feature_classes}
        if len(by_code) != len(self.feature_classes):
            raise ValueError("feature class codes must be unique")
        for entry in self.feature_classes:
            if entry.parent_code is not None and entry.parent_code not in by_code:
                raise ValueError(
                    f"feature class {entry.code} references unknown parent {entry.parent_code}"
                )
            if entry.parent_code == entry.code:
                raise ValueError(f"feature class {entry.code} cannot parent itself")

        for code in by_code:
            seen: set[str] = set()
            current = code
            while current is not None:
                if current in seen:
                    raise ValueError(f"feature class hierarchy contains a cycle at {current}")
                seen.add(current)
                current = by_code[current].parent_code

        roots = tuple(entry for entry in self.feature_classes if entry.parent_code is None)
        root_kinds = {entry.kind for entry in roots}
        if root_kinds != _ALL_FEATURE_KINDS or len(roots) != len(_ALL_FEATURE_KINDS):
            missing = ", ".join(sorted(_ALL_FEATURE_KINDS - root_kinds))
            extra = ", ".join(sorted(root_kinds - _ALL_FEATURE_KINDS))
            raise ValueError(
                "taxonomy must have exactly the five classification-axis roots; "
                f"missing={missing or '-'} extra={extra or '-'}"
            )
        for entry in self.feature_classes:
            if entry.parent_code is not None and by_code[entry.parent_code].kind != entry.kind:
                raise ValueError(
                    f"feature class {entry.code} changes kind below parent {entry.parent_code}"
                )
        return self

    def feature_class(self, code: str) -> MechanicalFeatureClass:
        for entry in self.feature_classes:
            if entry.code == code:
                return entry
        raise KeyError(f"unknown mechanical feature class: {code}")


def load_feature_taxonomy(path: Path) -> MechanicalFeatureTaxonomy:
    """Load one explicitly selected immutable taxonomy; no runtime discovery is used."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return MechanicalFeatureTaxonomy.model_validate(payload)


def experimental_mechanical_taxonomy() -> MechanicalFeatureTaxonomy:
    """Return the branch's pinned experimental taxonomy artifact."""

    return load_feature_taxonomy(
        Path(__file__).resolve().parent
        / "taxonomies"
        / "mechanical-features-1.0.0-experimental.json"
    )
