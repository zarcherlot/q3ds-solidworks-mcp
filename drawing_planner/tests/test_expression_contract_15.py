import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


_ROOT = Path(__file__).resolve().parents[2]
os.sys.path.insert(0, str(_ROOT))

from drawing_planner.validators import (  # noqa: E402
    ViewPlan15ExpressionValidator,
    ViewPlan15SchemaValidator,
)


class ViewPlan15ExpressionContractTests(unittest.TestCase):
    def setUp(self):
        fixture = _ROOT / "drawing_planner" / "tests" / "fixtures" / "view_plan.valid.json"
        self.plan = json.loads(fixture.read_text(encoding="utf-8"))
        self.plan["$schema"] = "https://local.example/schemas/solidworks/view-plan/1.5"
        self.plan["schema_version"] = "1.5"
        row = self.plan["feature_coverage"][0]
        row["feature_id"] = "FT-HOLE-1"
        row["feature_class"] = "geometry.hole.blind_drilled"
        for view in self.plan["views"]:
            view["expressed_features"] = ["FT-HOLE-1"]
            if view["section_definition"] is not None:
                view["section_definition"]["feature_ids"] = ["FT-HOLE-1"]
        row["requirements"] = [
            {
                "requirement_id": "opening",
                "requirement_kind": "opening_and_count",
                "expression_method": "direct_opening_view",
                "satisfied_by": [{"view_id": "front", "role": "primary"}],
                "minimum_independent_projections": 1,
                "status": "pass",
            },
            {
                "requirement_id": "depth",
                "requirement_kind": "depth_extent",
                "expression_method": "section_through_axis",
                "satisfied_by": [{"view_id": "section_A_A", "role": "primary"}],
                "minimum_independent_projections": 1,
                "status": "pass",
            },
        ]
        self.feature = SimpleNamespace(
            feature_id="FT-HOLE-1",
            feature_class="geometry.hole.blind_drilled",
            normal=(0.0, 0.0, -1.0),
            axis=SimpleNamespace(origin_m=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)),
            opening_count=1,
            axial_extent=SimpleNamespace(effective_depth_m=0.009, total_depth_m=0.01),
            occurrences=(SimpleNamespace(suppressed=False),),
            geometry_refs=SimpleNamespace(edge_ids=("B0E0",)),
        )
        self.relation = SimpleNamespace(
            relation_id="REL-PATTERN-1",
            relation_class="relation.pattern",
            member_feature_ids=("FT-HOLE-1",),
        )
        self.semantic = SimpleNamespace(
            features=(self.feature,), relations=(self.relation,)
        )
        for requirement in self.plan["feature_coverage"][0]["requirements"]:
            requirement.update(
                {
                    "expected_opening_count": 1
                    if requirement["requirement_kind"] == "opening_and_count" else None,
                    "expected_unsuppressed_occurrence_count": 1
                    if requirement["requirement_kind"] == "opening_and_count" else None,
                    "expected_effective_depth_m": 0.009
                    if requirement["requirement_kind"] == "depth_extent" else None,
                    "expected_total_depth_m": 0.01
                    if requirement["requirement_kind"] == "depth_extent" else None,
                    "expected_spatial_direction_model": None,
                    "semantic_relation_ids": [],
                    "discernibility_check": None,
                }
            )

    def test_schema_accepts_new_non_overlapping_expression_contract(self):
        self.assertEqual(ViewPlan15SchemaValidator().validate(self.plan), ())

    def test_schema_rejects_legacy_overlapping_fields(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][0]
        requirement["required_mode"] = requirement.pop("requirement_kind")
        requirement["expression_mode"] = requirement.pop("expression_method")
        issues = ViewPlan15SchemaValidator().validate(self.plan)
        self.assertTrue(issues)
        messages = " ".join(issue.message for issue in issues)
        self.assertIn("requirement_kind", messages)
        self.assertIn("expression_method", messages)

    def test_true_shape_uses_frozen_feature_direction(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][0]
        requirement["requirement_kind"] = "shape_true_form"
        requirement["expression_method"] = "true_shape_view"
        requirement["expected_opening_count"] = None
        requirement["expected_unsuppressed_occurrence_count"] = None
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertNotIn(
            "VP15-EXPRESSION-TRUE-SHAPE-DIRECTION",
            {issue.code for issue in issues},
        )
        self.plan["views"][0]["orientation"]["standard_view"] = "top"
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertIn(
            "VP15-EXPRESSION-TRUE-SHAPE-DIRECTION",
            {issue.code for issue in issues},
        )

    def test_independent_projection_gate_rejects_parallel_views(self):
        front = self.plan["views"][0]
        back = deepcopy(front)
        back["id"] = "back"
        back["orientation"]["standard_view"] = "back"
        self.plan["views"].append(back)
        requirement = self.plan["feature_coverage"][0]["requirements"][0]
        requirement.update(
            {
                "requirement_kind": "location_relation",
                "expression_method": "independent_multiview",
                "satisfied_by": [
                    {"view_id": "front", "role": "primary"},
                    {"view_id": "back", "role": "supporting"},
                ],
                "minimum_independent_projections": 2,
                "expected_opening_count": None,
                "expected_unsuppressed_occurrence_count": None,
                "semantic_relation_ids": ["REL-PATTERN-1"],
                "expected_spatial_direction_model": None,
            }
        )
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertIn(
            "VP15-EXPRESSION-INDEPENDENT-PROJECTIONS",
            {issue.code for issue in issues},
        )
        back["orientation"]["standard_view"] = "top"
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertNotIn(
            "VP15-EXPRESSION-INDEPENDENT-PROJECTIONS",
            {issue.code for issue in issues},
        )

    def test_count_and_depth_require_frozen_semantic_evidence(self):
        self.feature.opening_count = None
        self.feature.axial_extent = None
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        codes = {issue.code for issue in issues}
        self.assertIn("VP15-EXPRESSION-OPENING-COUNT-UNPROVEN", codes)
        self.assertIn("VP15-EXPRESSION-DEPTH-UNPROVEN", codes)

    def test_count_depth_and_relation_values_must_equal_frozen_evidence(self):
        requirements = self.plan["feature_coverage"][0]["requirements"]
        requirements[0]["expected_opening_count"] = 2
        requirements[0]["expected_unsuppressed_occurrence_count"] = 2
        requirements[1]["expected_effective_depth_m"] = 0.008
        requirements[1]["expected_total_depth_m"] = 0.02
        requirements.append(
            {
                "requirement_id": "pattern",
                "requirement_kind": "pattern_relation",
                "expression_method": "independent_multiview",
                "satisfied_by": [{"view_id": "front", "role": "primary"}],
                "minimum_independent_projections": 1,
                "expected_opening_count": None,
                "expected_unsuppressed_occurrence_count": None,
                "expected_effective_depth_m": None,
                "expected_total_depth_m": None,
                "expected_spatial_direction_model": None,
                "semantic_relation_ids": ["REL-MISSING-1"],
                "discernibility_check": None,
                "status": "pass",
            }
        )
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        codes = {issue.code for issue in issues}
        self.assertIn("VP15-EXPRESSION-OPENING-COUNT-MISMATCH", codes)
        self.assertIn("VP15-EXPRESSION-OCCURRENCE-COUNT-MISMATCH", codes)
        self.assertIn("VP15-EXPRESSION-EFFECTIVE-DEPTH-MISMATCH", codes)
        self.assertIn("VP15-EXPRESSION-TOTAL-DEPTH-MISMATCH", codes)
        self.assertIn("VP15-EXPRESSION-RELATION-MISSING", codes)

    def test_spatial_direction_must_match_frozen_axis(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][0]
        requirement.update(
            {
                "requirement_kind": "axis_direction",
                "expression_method": "independent_multiview",
                "expected_opening_count": None,
                "expected_unsuppressed_occurrence_count": None,
                "expected_spatial_direction_model": [1.0, 0.0, 0.0],
            }
        )
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertIn(
            "VP15-EXPRESSION-SPATIAL-DIRECTION-MISMATCH",
            {issue.code for issue in issues},
        )
        requirement["expected_spatial_direction_model"] = [0.0, 0.0, -1.0]
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertNotIn(
            "VP15-EXPRESSION-SPATIAL-DIRECTION-MISMATCH",
            {issue.code for issue in issues},
        )

    def test_section_path_and_depth_use_frozen_axis_extent(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][1]
        section = self.plan["views"][1]["section_definition"]
        section["cutting_line_points_model_m"] = [
            [-0.01, 0.0, 0.0],
            [0.01, 0.0, 0.0],
        ]
        section["section_depth_m"] = 0.005
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        codes = {issue.code for issue in issues}
        self.assertNotIn("VP15-EXPRESSION-SECTION-AXIS-MISS", codes)
        self.assertIn("VP15-EXPRESSION-SECTION-DEPTH", codes)
        section["cutting_line_points_model_m"] = [
            [-0.01, 0.01, 0.0],
            [0.01, 0.01, 0.0],
        ]
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertIn(
            "VP15-EXPRESSION-SECTION-AXIS-MISS",
            {issue.code for issue in issues},
        )

    def test_critical_discernibility_uses_frozen_edges_scale_and_line_width(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][0]
        requirement["discernibility_check"] = {
            "critical": True,
            "line_width_sheet_m": 0.0005,
            "minimum_line_width_ratio": 3.0,
        }
        geometry = {
            "bodies": [
                {
                    "edges": [
                        {
                            "id": "B0E0",
                            "start_model_m": [0.0, 0.0, 0.0],
                            "end_model_m": [0.001, 0.0, 0.0],
                        }
                    ]
                }
            ]
        }
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan,
            semantic_artifact=self.semantic,
            geometry_report=geometry,
        )
        self.assertIn(
            "VP15-EXPRESSION-DISCERNIBILITY",
            {issue.code for issue in issues},
        )
        self.plan["views"][0]["scale"] = 2.0
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan,
            semantic_artifact=self.semantic,
            geometry_report=geometry,
        )
        self.assertNotIn(
            "VP15-EXPRESSION-DISCERNIBILITY",
            {issue.code for issue in issues},
        )

    def test_half_section_requires_frozen_symmetry_relation(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][1]
        section_view = self.plan["views"][1]
        section_view["type"] = "half_section"
        section_view["section_definition"]["cutting_line_points_model_m"] = [
            [-0.01, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.01, 0.0],
        ]
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertIn(
            "VP15-EXPRESSION-HALF-SECTION-SYMMETRY",
            {issue.code for issue in issues},
        )
        symmetry = SimpleNamespace(
            relation_id="REL-SYMMETRY-1",
            relation_class="relation.symmetry_or_mirror",
            member_feature_ids=("FT-HOLE-1",),
            axis=None,
            plane_normal=(1.0, 0.0, 0.0),
        )
        self.semantic.relations = (*self.semantic.relations, symmetry)
        requirement["semantic_relation_ids"] = ["REL-SYMMETRY-1"]
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertNotIn(
            "VP15-EXPRESSION-HALF-SECTION-SYMMETRY",
            {issue.code for issue in issues},
        )

    def test_longitudinal_rib_section_is_rejected(self):
        self.feature.feature_class = "geometry.positive.rib"
        self.plan["feature_coverage"][0]["feature_class"] = "geometry.positive.rib"
        requirement = self.plan["feature_coverage"][0]["requirements"][1]
        self.plan["views"][1]["section_definition"]["cutting_line_points_model_m"] = [
            [0.0, 0.0, -0.01],
            [0.0, 0.0, 0.01],
        ]
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        self.assertIn(
            "VP15-EXPRESSION-RIB-LONGITUDINAL-SECTION",
            {issue.code for issue in issues},
        )

    def test_view_set_requires_exactly_one_primary_and_unique_views(self):
        requirement = self.plan["feature_coverage"][0]["requirements"][0]
        requirement["satisfied_by"] = [
            {"view_id": "front", "role": "supporting"},
            {"view_id": "front", "role": "supporting"},
        ]
        issues = ViewPlan15ExpressionValidator().validate(
            self.plan, semantic_artifact=self.semantic
        )
        codes = {issue.code for issue in issues}
        self.assertIn("VP15-EXPRESSION-PRIMARY-VIEW", codes)
        self.assertIn("VP15-EXPRESSION-DUPLICATE-VIEW", codes)


if __name__ == "__main__":
    unittest.main()
