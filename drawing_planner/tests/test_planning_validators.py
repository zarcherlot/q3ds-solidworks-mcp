import hashlib
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
os.sys.path.insert(0, str(_ROOT))

from drawing_planner.planning_models import PlanningRequest  # noqa: E402
from drawing_planner.planner_profiles import producer_contract_for_profile  # noqa: E402
from drawing_planner.validators import (  # noqa: E402
    FoundationViewPlanValidator,
    HandoffIntegrityValidator,
    RepositoryViewPlanValidator,
    ViewPlanCoverageValidator,
    ViewPlanLayoutValidator,
    ViewPlanSchemaValidator,
    ViewPlanSemanticsValidator,
)


class ViewPlanSchemaValidatorTests(unittest.TestCase):
    def setUp(self):
        fixture = (
            _ROOT
            / "drawing_planner"
            / "tests"
            / "fixtures"
            / "view_plan.valid.json"
        )
        self.plan = json.loads(fixture.read_text(encoding="utf-8"))
        self.validator = ViewPlanSchemaValidator()

    def test_complete_schema_14_fixture_passes(self):
        self.assertEqual(self.validator.validate(self.plan), ())

    def test_unknown_field_and_invalid_version_are_rejected_with_pointers(self):
        self.plan["unexpected"] = True
        self.plan["schema_version"] = "1.3"
        issues = self.validator.validate(self.plan)
        self.assertGreaterEqual(len(issues), 2)
        self.assertTrue(all(issue.gate == "schema" for issue in issues))
        self.assertIn("/schema_version", {issue.json_pointer for issue in issues})
        self.assertTrue(any("unexpected" in issue.message for issue in issues))

    def test_invalid_rfc3339_timestamp_is_rejected(self):
        self.plan["created_at_utc"] = "not-a-date"
        issues = self.validator.validate(self.plan)
        self.assertIn("/created_at_utc", {issue.json_pointer for issue in issues})

    def test_explicit_full_section_requires_points_space_and_direction(self):
        section = self.plan["views"][1]["section_definition"]
        section.update(
            {
                "cutting_plane_mode": "explicit_full",
                "cutting_line_points_model_m": [
                    [0.0125, -0.00632, -0.025],
                    [0.0125, 0.05752, -0.025],
                ],
                "cutting_line_coordinate_space": "model",
                "section_direction": [-1.0, 0.0, 0.0],
                "cutting_line_axis": None,
                "line_extension_ratio": None,
            }
        )
        self.assertEqual(self.validator.validate(self.plan), ())
        section.pop("section_direction")
        self.assertNotEqual(self.validator.validate(self.plan), ())


class HandoffIntegrityValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "part.SLDPRT"
        self.drawing = self.root / "blank.SLDDRW"
        self.readiness = self.root / "drawing-readiness.json"
        self.geometry = self.root / "model-geometry.json"
        self.model.write_bytes(b"model")
        self.drawing.write_bytes(b"blank drawing")
        self.readiness.write_text("{}", encoding="utf-8")
        self.geometry.write_text(
            json.dumps(
                {
                    "status": "success",
                    "part_box_m": {
                        "x_min_m": 0,
                        "y_min_m": 0,
                        "z_min_m": 0,
                        "x_max_m": 0.1,
                        "y_max_m": 0.1,
                        "z_max_m": 0.05,
                    },
                    "bodies": [
                        {
                            "faces": [
                                {
                                    "id": "B0F0",
                                    "loops": [],
                                    "surface_parameters": {
                                        "axis": [0, 0, 1],
                                        "origin": [0, 0, 0],
                                    },
                                }
                            ]
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.images = []
        for view in ("front", "back", "left", "right", "top", "bottom"):
            path = self.root / f"{view}.png"
            path.write_bytes(view.encode("ascii"))
            self.images.append({"view": view, "path": str(path), "sha256": _sha(path)})
        self.manifest = self.root / "drawing-planning-handoff.json"
        self.payload = {
            "protocol_id": "q3ds-drawing-planning-handoff",
            "schema_version": "1.0",
            "handoff_id": "DH-test-1",
            "status": "ready",
            "model": {
                "path": str(self.model),
                "sha256": _sha(self.model),
                "configuration": "Default",
                "display_state": "Display State-1",
            },
            "blank_drawing": {
                "path": str(self.drawing),
                "sha256": _sha(self.drawing),
                "blank": True,
            },
            "readiness_report": {
                "path": str(self.readiness),
                "sha256": _sha(self.readiness),
            },
            "geometry_report": {
                "path": str(self.geometry),
                "sha256": _sha(self.geometry),
            },
            "standard_view_images": self.images,
            "drawing_context": {
                "sheet": {
                    "name": "Sheet1",
                    "format_name": "A3-Landscape",
                    "width_m": 0.42,
                    "height_m": 0.297,
                },
                "projection_method": "first_angle",
                "sheet_scale": {"numerator": 1, "denominator": 1},
                "inner_frame": {
                    "bounds_sheet_m": {
                        "x_min_m": 0.01,
                        "y_min_m": 0.01,
                        "x_max_m": 0.41,
                        "y_max_m": 0.287,
                    },
                    "safe_zone_sheet_m": {
                        "x_min_m": 0.02,
                        "y_min_m": 0.02,
                        "x_max_m": 0.4,
                        "y_max_m": 0.277,
                    },
                },
                "reserved_zones": [],
            },
            "blocking_issues": [],
            "open_questions": [],
        }
        self._write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def _write_manifest(self):
        self.manifest.write_text(
            json.dumps(self.payload, sort_keys=True), encoding="utf-8"
        )

    def _request(self):
        return PlanningRequest(
            handoff_manifest_path=str(self.manifest),
            handoff_manifest_sha256=_sha(self.manifest),
            publication_directory=str(self.root),
        )

    def test_complete_unchanged_handoff_passes(self):
        result = HandoffIntegrityValidator().validate(self._request())
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.manifest["handoff_id"], "DH-test-1")

    def test_manifest_hash_mismatch_stops_before_parsing(self):
        request = self._request()
        self.manifest.write_text("not json", encoding="utf-8")
        result = HandoffIntegrityValidator().validate(request)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.issues[0].code, "VP-INTEGRITY-MANIFEST-HASH")

    def test_changed_artifact_is_rejected(self):
        request = self._request()
        self.geometry.write_text('{"changed":true}', encoding="utf-8")
        result = HandoffIntegrityValidator().validate(request)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.issues[0].code, "VP-INTEGRITY-ARTIFACT-HASH")

    def test_publication_must_share_the_verified_handoff_directory(self):
        other = self.root / "other"
        other.mkdir()
        request = PlanningRequest(
            handoff_manifest_path=str(self.manifest),
            handoff_manifest_sha256=_sha(self.manifest),
            publication_directory=str(other),
        )
        result = HandoffIntegrityValidator().validate(request)
        self.assertEqual(result.status, "fail")
        self.assertEqual(
            result.issues[0].code,
            "VP-INTEGRITY-PUBLICATION-LOCATION",
        )

    def test_duplicate_standard_view_and_unknown_field_are_rejected(self):
        self.payload["standard_view_images"][-1]["view"] = "front"
        self.payload["unexpected"] = True
        self._write_manifest()
        result = HandoffIntegrityValidator().validate(self._request())
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.issues[0].code, "VP-INTEGRITY-MANIFEST-CONTRACT")

    def test_plan_must_preserve_all_handoff_bindings(self):
        plan = self._bound_plan()
        validator = HandoffIntegrityValidator()
        self.assertEqual(
            validator.validate_plan_bindings(plan, self._request()).status, "pass"
        )
        plan["geometry_report_sha256"] = "0" * 64
        result = validator.validate_plan_bindings(plan, self._request())
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.issues[0].code, "VP-INTEGRITY-PLAN-BINDING")

    def test_repository_pipeline_passes_all_deterministic_gates(self):
        result = RepositoryViewPlanValidator().validate(
            self._bound_plan(), self._request()
        )
        self.assertEqual(result.integrity, "pass")
        self.assertEqual(result.schema_check, "pass")
        self.assertEqual(result.semantics, "pass")
        self.assertEqual(result.coverage, "pass")
        self.assertEqual(result.layout, "pass")
        self.assertTrue(result.passed)
        self.assertIs(FoundationViewPlanValidator, RepositoryViewPlanValidator)

    def test_repository_pipeline_accepts_authoritative_explicit_full_section(self):
        plan = self._bound_plan()
        plan["views"][1]["section_definition"].update(
            {
                "cutting_plane_mode": "explicit_full",
                "cutting_line_points_model_m": [
                    [0.0125, -0.00632, -0.025],
                    [0.0125, 0.05752, -0.025],
                ],
                "cutting_line_coordinate_space": "model",
                "section_direction": [-1.0, 0.0, 0.0],
                "cutting_line_axis": None,
                "line_extension_ratio": None,
                "reverse_direction": False,
            }
        )
        result = RepositoryViewPlanValidator().validate(plan, self._request())
        self.assertTrue(result.passed, result.issues)

    def test_repository_pipeline_rejects_untrusted_ruleset_identity(self):
        plan = self._bound_plan()
        plan["producer"]["ruleset_sha256"] = "f" * 64
        result = RepositoryViewPlanValidator().validate(plan, self._request())
        self.assertEqual(result.semantics, "fail")
        self.assertIn(
            "VP-SEMANTICS-PRODUCER-TRUST",
            {issue.code for issue in result.issues},
        )

    def test_semantics_rejects_parent_self_and_missing_evidence_pointer(self):
        plan = self._bound_plan()
        section = plan["views"][1]
        section["parent_view_id"] = section["id"]
        section["source"]["reference"] = section["id"]
        section["model_evidence"][0]["json_pointer"] = "/missing"
        issues = ViewPlanSemanticsValidator().validate(plan)
        codes = {issue.code for issue in issues}
        self.assertIn("VP-SEMANTICS-PARENT-SELF", codes)
        self.assertIn("VP-SEMANTICS-EVIDENCE-POINTER", codes)

    def test_semantics_rejects_nonperpendicular_half_section(self):
        plan = self._bound_plan()
        section = plan["views"][1]
        section["type"] = "half_section"
        section["section_definition"].update(
            {
                "cutting_plane_mode": "explicit_half",
                "cutting_line_points_model_m": [
                    [0.0, 0.0, 0.0],
                    [0.01, 0.0, 0.0],
                    [0.02, 0.0, 0.0],
                ],
                "cutting_line_axis": None,
                "line_extension_ratio": None,
            }
        )
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertIn(
            "VP-SEMANTICS-HALF-SECTION-PERPENDICULAR",
            {issue.code for issue in issues},
        )

    def test_semantics_treats_explicit_full_section_points_as_authoritative(self):
        plan = self._bound_plan()
        definition = plan["views"][1]["section_definition"]
        definition.update(
            {
                "cutting_plane_mode": "explicit_full",
                "cutting_line_points_model_m": [
                    [0.0125, -0.00632, -0.025],
                    [0.0125, 0.05752, -0.025],
                ],
                "cutting_line_coordinate_space": "model",
                "section_direction": [-1.0, 0.0, 0.0],
                "cutting_line_axis": None,
                "line_extension_ratio": None,
            }
        )
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertNotIn(
            "VP-SEMANTICS-SECTION-FEATURE-AXIS",
            {issue.code for issue in issues},
        )
        definition["section_direction"] = [0.0, 1.0, 0.0]
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertIn(
            "VP-SEMANTICS-SECTION-DIRECTION-PERPENDICULAR",
            {issue.code for issue in issues},
        )

    def test_semantics_validates_offset_feature_axis_against_finite_segments(self):
        plan = self._bound_plan()
        section = plan["views"][1]
        section["type"] = "offset_section"
        section["section_definition"].update(
            {
                "cutting_plane_mode": "explicit_offset",
                "cutting_line_points_model_m": [
                    [-0.01, 0.0, 0.0],
                    [0.0, 0.0, 0.0],
                    [0.0, 0.01, 0.0],
                    [0.01, 0.01, 0.0],
                ],
                "cutting_line_axis": None,
                "line_extension_ratio": None,
            }
        )
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertNotIn(
            "VP-SEMANTICS-OFFSET-FEATURE-AXIS",
            {issue.code for issue in issues},
        )
        section["section_definition"]["cutting_line_points_model_m"] = [
            [0.02, 0.02, 0.0],
            [0.03, 0.02, 0.0],
            [0.03, 0.03, 0.0],
            [0.04, 0.03, 0.0],
        ]
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertIn(
            "VP-SEMANTICS-OFFSET-FEATURE-AXIS",
            {issue.code for issue in issues},
        )

    def test_semantics_rejects_inconsistent_projected_full_section_position(self):
        plan = self._bound_plan()
        section = plan["views"][1]
        section["section_definition"]["cutting_line_axis"] = "horizontal"
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertIn(
            "VP-SEMANTICS-SECTION-PLACEMENT",
            {issue.code for issue in issues},
        )

    def test_semantics_enforces_explicit_basis_policy_and_orthogonality(self):
        plan = self._bound_plan()
        plan["views"][0]["orientation"] = {
            "kind": "explicit_basis",
            "view_direction_model": [1.0, 0.0, 0.0],
            "up_direction_model": [1.0, 0.0, 0.0],
            "roll_angle_rad": 0.0,
        }
        issues = ViewPlanSemanticsValidator().validate(plan)
        codes = {issue.code for issue in issues}
        self.assertIn("VP-SEMANTICS-EXPLICIT-BASIS-POLICY", codes)
        self.assertIn("VP-SEMANTICS-EXPLICIT-BASIS-ORTHOGONAL", codes)

    def test_semantics_enforces_first_angle_projected_position(self):
        plan = self._bound_plan()
        parent = plan["views"][0]
        projected = deepcopy(parent)
        projected.update(
            {
                "id": "projected-top",
                "type": "projected_view",
                "source": {
                    "kind": "parent_view",
                    "reference": "front",
                    "projection_direction": "up",
                },
                "orientation": {"kind": "derived_from_parent"},
                "parent_view_id": "front",
                "alignment": "projected",
                "center_marks": [],
                "symmetry_centerlines": [],
                "position_sheet_m": [parent["position_sheet_m"][0], 0.06],
                "placement_box": {
                    "x_min_m": 0.06,
                    "y_min_m": 0.03,
                    "x_max_m": 0.17,
                    "y_max_m": 0.09,
                },
            }
        )
        plan["views"].append(projected)
        plan["decision_summary"]["final_minimum_view_set"].append(
            {"view_id": "projected-top", "omission_impact": "test"}
        )
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertNotIn(
            "VP-SEMANTICS-PROJECTION-METHOD",
            {issue.code for issue in issues},
        )
        projected["position_sheet_m"][1] = 0.26
        issues = ViewPlanSemanticsValidator().validate(plan)
        self.assertIn(
            "VP-SEMANTICS-PROJECTION-METHOD",
            {issue.code for issue in issues},
        )

    def test_coverage_rejects_mode_downgrade_and_missing_forced_feature(self):
        plan = self._bound_plan()
        requirement = plan["feature_coverage"][0]["requirements"][1]
        requirement["required_mode"] = "direct_visible_profile"
        requirement["satisfied_by"] = "front"
        plan["views"][0]["center_marks"][0]["feature_ids"].append("B0F1")
        issues = ViewPlanCoverageValidator().validate(plan)
        codes = {issue.code for issue in issues}
        self.assertIn("VP-COVERAGE-MODE-MISMATCH", codes)
        self.assertIn("VP-COVERAGE-INCOMPATIBLE-VIEW", codes)
        self.assertIn("VP-COVERAGE-FEATURE-MISSING", codes)

    def test_layout_rejects_overlap_reserved_zone_and_shallow_dimension_band(self):
        plan = self._bound_plan()
        plan["views"][1]["placement_box"] = dict(plan["views"][0]["placement_box"])
        plan["views"][1]["position_sheet_m"] = list(plan["views"][0]["position_sheet_m"])
        plan["reserved_zones"].append(
            {
                "id": "reserved-test",
                "kind": "template_reserved",
                "bounds_sheet_m": {
                    "x_min_m": 0.1,
                    "y_min_m": 0.1,
                    "x_max_m": 0.15,
                    "y_max_m": 0.15,
                },
                "source": "unit-test",
            }
        )
        plan["dimension_zones"][0]["required_depth_m"] = 0.01
        issues = ViewPlanLayoutValidator().validate(plan)
        codes = {issue.code for issue in issues}
        self.assertIn("VP-LAYOUT-VIEW-OVERLAP", codes)
        self.assertIn("VP-LAYOUT-PLACEMENT-RESERVED", codes)
        self.assertIn("VP-LAYOUT-DIMENSION-DEPTH-POLICY", codes)

    def test_layout_rejects_c2_profiles_outside_source_view_box(self):
        plan = self._bound_plan()
        detail = plan["views"][1]
        detail["type"] = "detail_view"
        detail["parent_view_id"] = "front"
        detail["detail_definition"] = {
            "center_offset_from_parent_m": [0.05, 0.0],
            "radius_sheet_m": 0.01,
        }
        issues = ViewPlanLayoutValidator().validate(plan)
        self.assertIn(
            "VP-LAYOUT-DETAIL-PROFILE",
            {issue.code for issue in issues},
        )

        detail["type"] = "broken_out_section"
        detail["parent_view_id"] = None
        detail["broken_out_definition"] = {
            "center_offset_from_view_m": [0.055, 0.0],
            "radius_sheet_m": 0.01,
        }
        issues = ViewPlanLayoutValidator().validate(plan)
        self.assertIn(
            "VP-LAYOUT-BROKEN-OUT-PROFILE",
            {issue.code for issue in issues},
        )

    def test_pipeline_stops_after_integrity_or_schema_precondition_failure(self):
        request = self._request()
        self.geometry.write_text('{"changed":true}', encoding="utf-8")
        result = RepositoryViewPlanValidator().validate(self._bound_plan(), request)
        self.assertEqual(result.integrity, "fail")
        self.assertEqual(result.schema_check, "not_run")
        self.assertEqual(result.semantics, "not_run")

        self.geometry.write_text(
            json.dumps(
                {
                    "status": "success",
                    "part_box_m": {},
                    "bodies": [{"faces": [{"id": "B0F0"}]}],
                }
            ),
            encoding="utf-8",
        )
        self.payload["geometry_report"]["sha256"] = _sha(self.geometry)
        self._write_manifest()
        request = self._request()
        plan = self._bound_plan()
        plan["unexpected"] = True
        result = RepositoryViewPlanValidator().validate(plan, request)
        self.assertEqual(result.integrity, "pass")
        self.assertEqual(result.schema_check, "fail")
        self.assertEqual(result.semantics, "not_run")
        self.assertEqual(result.coverage, "not_run")
        self.assertEqual(result.layout, "not_run")

    def _bound_plan(self):
        fixture = (
            _ROOT
            / "drawing_planner"
            / "tests"
            / "fixtures"
            / "view_plan.valid.json"
        )
        plan = json.loads(fixture.read_text(encoding="utf-8"))
        plan.update(
            {
                "model_path": str(self.model),
                "model_sha256": _sha(self.model),
                "drawing_path": str(self.drawing),
                "drawing_sha256": _sha(self.drawing),
                "readiness_report_path": str(self.readiness),
                "readiness_report_sha256": _sha(self.readiness),
                "geometry_report_path": str(self.geometry),
                "geometry_report_sha256": _sha(self.geometry),
                "configuration": "Default",
                "display_state": "Display State-1",
                "standard_view_images": self.images,
                "producer": producer_contract_for_profile("production"),
                **self.payload["drawing_context"],
            }
        )
        for view in plan["views"]:
            for evidence in view["model_evidence"]:
                evidence["report_path"] = str(self.geometry)
            for centerline in view["symmetry_centerlines"]:
                for evidence in centerline["model_evidence"]:
                    evidence["report_path"] = str(self.geometry)
        return plan


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
