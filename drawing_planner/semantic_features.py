"""Experimental semantic-feature artifact contracts and closed-set coverage checks."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from drawing_planner.feature_taxonomy import MechanicalFeatureTaxonomy


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
FeatureId = Annotated[str, StringConstraints(pattern=r"^FT-[A-Z0-9]+(?:-[A-Z0-9]+)*$", max_length=128)]
RelationId = Annotated[str, StringConstraints(pattern=r"^REL-[A-Z0-9]+(?:-[A-Z0-9]+)*$", max_length=128)]
Point3 = tuple[float, float, float]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def freeze_json_arrays(cls, value):
        return tuple(value) if isinstance(value, list) else value


class SemanticFeatureProducer(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    extraction_mode: Literal["csharp_initializer", "offline_fixture", "controlled_manual_truth"]


class ModelBinding(_StrictModel):
    path: str = Field(min_length=1)
    sha256: Sha256
    configuration: str = Field(min_length=1)
    display_state: str = Field(min_length=1)


class ArtifactBinding(_StrictModel):
    path: str = Field(min_length=1)
    sha256: Sha256


class TaxonomyBinding(ArtifactBinding):
    taxonomy_id: Literal["mechanical-features"]
    taxonomy_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[a-z0-9.-]+)?$")


class GeometryRefs(_StrictModel):
    body_ids: tuple[Annotated[str, StringConstraints(pattern=r"^B[0-9]+$")], ...] = Field(min_length=1)
    face_ids: tuple[Annotated[str, StringConstraints(pattern=r"^B[0-9]+F[0-9]+$")], ...] = ()
    edge_ids: tuple[Annotated[str, StringConstraints(pattern=r"^B[0-9]+E[0-9]+$")], ...] = ()
    vertex_ids: tuple[Annotated[str, StringConstraints(pattern=r"^B[0-9]+V[0-9]+$")], ...] = ()

    @model_validator(mode="after")
    def unique_refs(self) -> "GeometryRefs":
        for label in ("body_ids", "face_ids", "edge_ids", "vertex_ids"):
            values = getattr(self, label)
            if len(values) != len(set(values)):
                raise ValueError(f"geometry_refs.{label} must not contain duplicates")
        return self


class FeatureAxis(_StrictModel):
    origin_m: Point3
    direction: Point3

    @model_validator(mode="after")
    def nonzero_finite_direction(self) -> "FeatureAxis":
        values = (*self.origin_m, *self.direction)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("feature axis values must be finite")
        if math.sqrt(sum(value * value for value in self.direction)) <= 1e-12:
            raise ValueError("feature axis direction must be non-zero")
        return self


class AxialExtent(_StrictModel):
    start_m: float
    end_m: float
    effective_depth_m: float | None
    total_depth_m: float = Field(ge=0)
    bottom_form: Literal["through", "flat", "conical", "spherical", "compound", "unknown"]

    @model_validator(mode="after")
    def consistent_extent(self) -> "AxialExtent":
        if not all(math.isfinite(value) for value in (self.start_m, self.end_m, self.total_depth_m)):
            raise ValueError("axial extent must contain finite values")
        if self.effective_depth_m is not None:
            if not math.isfinite(self.effective_depth_m) or self.effective_depth_m < 0:
                raise ValueError("effective depth must be finite and non-negative")
            if self.effective_depth_m > self.total_depth_m + 1e-12:
                raise ValueError("effective depth cannot exceed total depth")
        if abs(abs(self.end_m - self.start_m) - self.total_depth_m) > 1e-9:
            raise ValueError("total depth must equal the frozen axial span")
        if self.bottom_form == "through" and self.effective_depth_m != self.total_depth_m:
            raise ValueError("through extent effective depth must equal total depth")
        return self


class FeatureOccurrence(_StrictModel):
    occurrence_id: StableId
    suppressed: bool
    geometry_refs: GeometryRefs


class SemanticFeature(_StrictModel):
    feature_id: FeatureId
    feature_class: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", max_length=128)
    parent_feature_id: FeatureId | None
    source_feature_ref: str | None
    significance: tuple[Literal["manufacturing", "assembly", "function", "inspection"], ...] = Field(min_length=1)
    geometry_refs: GeometryRefs
    axis: FeatureAxis | None
    normal: Point3 | None
    opening_count: int | None = Field(ge=0)
    axial_extent: AxialExtent | None
    occurrences: tuple[FeatureOccurrence, ...] = ()
    evidence_status: Literal["complete", "partial"]

    @model_validator(mode="after")
    def local_consistency(self) -> "SemanticFeature":
        if len(self.significance) != len(set(self.significance)):
            raise ValueError("feature significance must not contain duplicates")
        occurrence_ids = [row.occurrence_id for row in self.occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("feature occurrence IDs must be unique")
        if self.normal is not None:
            if any(not math.isfinite(value) for value in self.normal):
                raise ValueError("feature normal must contain finite values")
            if math.sqrt(sum(value * value for value in self.normal)) <= 1e-12:
                raise ValueError("feature normal must be non-zero")
        if self.opening_count is not None and self.occurrences:
            unsuppressed = sum(not occurrence.suppressed for occurrence in self.occurrences)
            if self.opening_count != unsuppressed:
                raise ValueError("opening_count must equal the unsuppressed occurrence count")
        return self


class SemanticRelation(_StrictModel):
    relation_id: RelationId
    relation_class: Literal["relation.pattern", "relation.symmetry_or_mirror", "relation.coaxial_or_intersecting"]
    member_feature_ids: tuple[FeatureId, ...] = Field(min_length=1)
    axis: FeatureAxis | None
    plane_normal: Point3 | None
    evidence_status: Literal["complete", "partial"]

    @model_validator(mode="after")
    def unique_members(self) -> "SemanticRelation":
        if len(self.member_feature_ids) != len(set(self.member_feature_ids)):
            raise ValueError("relation member feature IDs must be unique")
        if self.relation_class == "relation.pattern" and len(self.member_feature_ids) != 1:
            raise ValueError("pattern relation must reference one grouped semantic feature")
        if self.relation_class == "relation.symmetry_or_mirror" and self.plane_normal is None:
            raise ValueError("symmetry or mirror relation requires a plane normal")
        if self.relation_class in {"relation.pattern", "relation.coaxial_or_intersecting"} and self.axis is None:
            raise ValueError(f"{self.relation_class} requires an axis")
        return self


class FeatureExemption(_StrictModel):
    feature_id: FeatureId
    reason_code: Literal["not_drawing_significant", "covered_by_parent", "controlled_scope_exclusion"]
    reason: str = Field(min_length=1, max_length=1000)
    controlled_source: str = Field(min_length=1, max_length=500)


class SemanticOpenQuestion(_StrictModel):
    question_id: StableId
    code: Literal["UNCLASSIFIED_FEATURE", "FEATURE_GEOMETRY_INCOMPLETE", "FUNCTIONAL_DATUM_UNKNOWN", "CONTROLLED_SEMANTICS_REQUIRED"]
    feature_ids: tuple[FeatureId, ...] = ()
    impact: str = Field(min_length=1, max_length=1000)
    required_source: str = Field(min_length=1, max_length=500)


class ModelSemanticFeatures(_StrictModel):
    protocol_id: Literal["q3ds-solidworks-model-semantic-features"]
    schema_version: Literal["1.0"]
    artifact_id: StableId
    status: Literal["complete", "incomplete"]
    producer: SemanticFeatureProducer
    model: ModelBinding
    geometry_report: ArtifactBinding
    taxonomy: TaxonomyBinding
    features: tuple[SemanticFeature, ...] = Field(min_length=1)
    relations: tuple[SemanticRelation, ...] = ()
    required_feature_ids: tuple[FeatureId, ...] = ()
    exemptions: tuple[FeatureExemption, ...] = ()
    open_questions: tuple[SemanticOpenQuestion, ...] = ()

    @model_validator(mode="after")
    def graph_consistency(self) -> "ModelSemanticFeatures":
        by_id = {feature.feature_id: feature for feature in self.features}
        if len(by_id) != len(self.features):
            raise ValueError("semantic feature IDs must be unique")
        for feature in self.features:
            if feature.parent_feature_id is not None and feature.parent_feature_id not in by_id:
                raise ValueError(f"feature {feature.feature_id} references unknown parent {feature.parent_feature_id}")
            if feature.parent_feature_id == feature.feature_id:
                raise ValueError(f"feature {feature.feature_id} cannot parent itself")
        for feature_id in by_id:
            seen: set[str] = set()
            current: str | None = feature_id
            while current is not None:
                if current in seen:
                    raise ValueError(f"semantic feature hierarchy contains a cycle at {current}")
                seen.add(current)
                current = by_id[current].parent_feature_id

        required = set(self.required_feature_ids)
        if len(required) != len(self.required_feature_ids):
            raise ValueError("required_feature_ids must not contain duplicates")
        unknown_required = required - set(by_id)
        if unknown_required:
            raise ValueError(f"required_feature_ids contain unknown features: {', '.join(sorted(unknown_required))}")
        if any(by_id[feature_id].evidence_status != "complete" for feature_id in required):
            raise ValueError("required features must have complete semantic evidence")

        exemption_ids = [row.feature_id for row in self.exemptions]
        if len(exemption_ids) != len(set(exemption_ids)):
            raise ValueError("feature exemptions must not contain duplicate feature IDs")
        if set(exemption_ids) - set(by_id):
            raise ValueError("feature exemptions reference unknown features")
        if required & set(exemption_ids):
            raise ValueError("a feature cannot be both required and exempt")

        relation_ids = [row.relation_id for row in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("semantic relation IDs must be unique")
        for relation in self.relations:
            unknown = set(relation.member_feature_ids) - set(by_id)
            if unknown:
                raise ValueError(f"relation {relation.relation_id} references unknown features")

        question_ids = [row.question_id for row in self.open_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("open question IDs must be unique")
        for question in self.open_questions:
            if set(question.feature_ids) - set(by_id):
                raise ValueError(f"open question {question.question_id} references unknown features")
        if self.status == "complete" and self.open_questions:
            raise ValueError("complete semantic artifact cannot contain open questions")
        if self.status == "incomplete" and not self.open_questions:
            raise ValueError("incomplete semantic artifact requires open questions")
        return self

    def validate_bindings(self, taxonomy: MechanicalFeatureTaxonomy, geometry: Mapping) -> None:
        if self.taxonomy.taxonomy_id != taxonomy.taxonomy_id or self.taxonomy.taxonomy_version != taxonomy.taxonomy_version:
            raise ValueError("semantic artifact taxonomy identity does not match the selected taxonomy")
        valid_classes = {row.code for row in taxonomy.feature_classes if not row.abstract}
        for feature in self.features:
            if feature.feature_class not in valid_classes:
                raise ValueError(f"feature {feature.feature_id} uses unknown or abstract class {feature.feature_class}")

        body_ids: set[str] = set()
        face_ids: set[str] = set()
        edge_ids: set[str] = set()
        vertex_ids: set[str] = set()
        for body in geometry.get("bodies", ()):
            body_ids.add(body.get("id"))
            for face in body.get("faces", ()):
                face_ids.add(face.get("id"))
                edge_ids.update(face.get("edge_ids", ()))
            for edge in body.get("edges", ()):
                edge_ids.add(edge.get("id"))
            for vertex in body.get("vertices", ()):
                vertex_ids.add(vertex.get("id"))
        for feature in self.features:
            for occurrence in (None, *feature.occurrences):
                refs = feature.geometry_refs if occurrence is None else occurrence.geometry_refs
                _require_subset(refs.body_ids, body_ids, feature.feature_id, "body")
                _require_subset(refs.face_ids, face_ids, feature.feature_id, "face")
                _require_subset(refs.edge_ids, edge_ids, feature.feature_id, "edge")
                _require_subset(refs.vertex_ids, vertex_ids, feature.feature_id, "vertex")


class ClosedSetCoverageResult(_StrictModel):
    status: Literal["pass", "fail"]
    missing_feature_ids: tuple[FeatureId, ...] = ()
    unexpected_feature_ids: tuple[FeatureId, ...] = ()
    duplicate_feature_ids: tuple[FeatureId, ...] = ()
    invalid_exemption_ids: tuple[FeatureId, ...] = ()


def assess_closed_set_coverage(
    semantic_artifact: ModelSemanticFeatures,
    covered_feature_ids: Sequence[str],
) -> ClosedSetCoverageResult:
    covered = tuple(covered_feature_ids)
    duplicates = tuple(sorted({value for value in covered if covered.count(value) > 1}))
    covered_set = set(covered)
    known = {feature.feature_id for feature in semantic_artifact.features}
    required = set(semantic_artifact.required_feature_ids)
    exempt = {row.feature_id for row in semantic_artifact.exemptions}
    missing = tuple(sorted(required - covered_set))
    unexpected = tuple(sorted(covered_set - known))
    invalid_exemptions = tuple(sorted(exempt & covered_set))
    failed = bool(missing or unexpected or duplicates or invalid_exemptions)
    return ClosedSetCoverageResult(
        status="fail" if failed else "pass",
        missing_feature_ids=missing,
        unexpected_feature_ids=unexpected,
        duplicate_feature_ids=duplicates,
        invalid_exemption_ids=invalid_exemptions,
    )


def load_model_semantic_features(
    path: Path,
    *,
    taxonomy: MechanicalFeatureTaxonomy,
    verify_files: bool = True,
) -> ModelSemanticFeatures:
    artifact = ModelSemanticFeatures.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if verify_files:
        _verify_file(artifact.model.path, artifact.model.sha256, "model")
        _verify_file(artifact.geometry_report.path, artifact.geometry_report.sha256, "geometry report")
        _verify_file(artifact.taxonomy.path, artifact.taxonomy.sha256, "taxonomy")
    geometry = json.loads(Path(artifact.geometry_report.path).read_text(encoding="utf-8"))
    artifact.validate_bindings(taxonomy, geometry)
    return artifact


def _verify_file(path: str, expected_sha256: str, label: str) -> None:
    target = Path(path)
    if not target.is_absolute() or not target.is_file():
        raise ValueError(f"bound {label} path must be an existing absolute file: {path}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"bound {label} SHA-256 mismatch")


def _require_subset(values, allowed, feature_id: str, kind: str) -> None:
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"feature {feature_id} references unknown {kind} IDs: {', '.join(sorted(unknown))}")
