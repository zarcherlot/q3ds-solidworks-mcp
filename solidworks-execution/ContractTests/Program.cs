using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Infrastructure;
using SolidworksExecution.Models;
using SolidworksExecution.Services;

namespace SolidworksExecution.ContractTests
{
    internal static class Program
    {
        private static int _passed;

        private static int Main(string[] args)
        {
            try
            {
                if (args.Length != 1 || !Path.IsPathRooted(args[0]))
                    throw new ArgumentException("Pass the absolute repository root as the only argument.");
                string root = Path.GetFullPath(args[0]);
                string schemaPath = Path.Combine(root, "drawing_planner", "contracts",
                    "view-plan.schema.json");
                string fixturePath = Path.Combine(root, "drawing_planner", "tests", "fixtures",
                    "view_plan.valid.json");

                var validator = new ViewPlanContractValidator(schemaPath);
                AssertEqual(ViewPlanContractValidator.ContractSha256,
                    "2bc4bc1b8b0c6ffae64a1e6906cfb0f88055d13839228578ff48e5b724556c9f",
                    "contract hash constant");

                JObject valid = ReadJsonObjectWithoutDateCoercion(fixturePath);
                ViewPlanDocument parsed;
                ViewPlanContractError error;
                Assert(validator.TryParse(valid, out parsed, out error),
                    "valid fixture should parse: " + Format(error));
                AssertEqual(parsed.ProtocolId, "solidworks-view-plan", "protocol id");
                AssertEqual(parsed.SchemaVersion, "1.4", "schema version");
                Assert(parsed.ViewTypes.Length == ((JArray)valid["views"]).Count,
                    "view count should be preserved");
                Pass("complete ViewPlan 1.4 fixture");

                JObject reordered = ReverseProperties(valid);
                ViewPlanDocument reorderedPlan;
                Assert(validator.TryParse(reordered, out reorderedPlan, out error),
                    "reordered fixture should parse: " + Format(error));
                AssertEqual(parsed.CanonicalSha256, reorderedPlan.CanonicalSha256,
                    "canonical hash must ignore property order");
                Pass("canonical plan hash");

                JObject unknown = (JObject)valid.DeepClone();
                unknown["unexpected"] = true;
                AssertInvalid(validator, unknown, "/unexpected");
                Pass("unknown field rejection");

                JObject wrongVersion = (JObject)valid.DeepClone();
                wrongVersion["schema_version"] = "1.3";
                AssertInvalid(validator, wrongVersion, "/schema_version");
                Pass("schema version rejection");

                JObject badTime = (JObject)valid.DeepClone();
                badTime["created_at_utc"] = "2026-08-08 12:00:00";
                AssertInvalid(validator, badTime, "/created_at_utc");
                Pass("RFC 3339 format rejection");

                JObject missingProducer = (JObject)valid.DeepClone();
                missingProducer.Property("producer").Remove();
                AssertInvalid(validator, missingProducer, "/producer");
                Pass("required field rejection");

                JObject invalidTuple = (JObject)valid.DeepClone();
                ((JArray)invalidTuple["views"][0]["position_sheet_m"]).Add(0.1);
                AssertInvalid(validator, invalidTuple, "/views/0/position_sheet_m");
                Pass("tuple length rejection");

                JObject invalidUnion = (JObject)valid.DeepClone();
                invalidUnion["views"][0]["source"]["kind"] = "parent_view";
                AssertInvalid(validator, invalidUnion, "/views/0/source/kind");
                Pass("discriminated union rejection");

                ViewPlanDocument ignoredPlan;
                Assert(!validator.TryParse(new JArray(), out ignoredPlan, out error),
                    "non-object candidate must fail");
                AssertEqual(error.JsonPointer, "", "non-object pointer");
                Pass("structured object requirement");

                var guard = new TestGuard(7);
                var service = new SolidWorksService(guard);
                ExecutionResponse response = service.ValidatePartDrawingViewPlan(
                    new ToolRequest
                    {
                        OperationId = "contract-test-1",
                        Tool = "validate_part_drawing_view_plan",
                        StateVersion = 7,
                        Params = PlanParameters(valid)
                    });
                AssertEqual(response.Status, "COMPLETED", "private service validation status");
                Assert(response.Verified, "private service result must be verified");
                Assert(response.StateVersion == 7 && guard.GetCurrentStateVersion() == 7,
                    "read-only validation must not mutate state_version");
                var result = response.ResultGeometry as JObject;
                Assert(result != null && result.Value<bool>("contract_valid"),
                    "private service must report contract_valid");
                Assert(result.Value<bool>("solidworks_contacted") == false,
                    "private service must not contact SolidWorks");
                AssertEqual(result.Value<string>("b2_basic_view_subset"), "compilable",
                    "full fixture C4 capability assessment");
                AssertEqual(result.Value<string>("execution_readiness"), "supported",
                    "full fixture execution readiness");
                Pass("private COM-free execution-service entry");

                var basicCompiler = new ViewPlanBasicExecutionCompiler();
                JObject basicCandidate = BuildBasicViewPlan(valid, true);
                ViewPlanDocument basicDocument;
                Assert(validator.TryParse(basicCandidate, out basicDocument, out error),
                    "basic-view candidate should satisfy ViewPlan 1.4: " + Format(error));
                ViewPlanBasicExecutionPlan basicPlan;
                ViewPlanExecutionContractError executionError;
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                    "basic-view candidate should compile: " + Format(executionError));
                Assert(basicPlan.Views.Count == 2, "basic plan should contain two views");
                AssertEqual(basicPlan.Views[0].Id, "front", "topological parent order");
                AssertEqual(basicPlan.Views[1].Id, "projected-right", "topological child order");
                Assert(basicPlan.InputArtifacts.Count == 10,
                    "B3 execution plan should bind model, drawing, two reports, and six images");
                Pass("B2 model/projected compilation and stable topological order");

                foreach (string sectionType in new[] { "full_section", "half_section",
                    "offset_section", "aligned_section", "removed_section" })
                {
                    JObject sectionCandidate = BuildSectionViewPlan(valid, sectionType);
                    Assert(validator.TryParse(sectionCandidate, out basicDocument, out error),
                        sectionType + " candidate should satisfy ViewPlan 1.4: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                        out executionError), sectionType + " should compile: " +
                        Format(executionError));
                    Assert(basicPlan.Views.Count == 2 && basicPlan.Views[0].Id == "front" &&
                        basicPlan.Views[1].Type == sectionType,
                        sectionType + " should retain parent-first native creation order");
                    AssertEqual(basicPlan.Views[1].SectionLabel,
                        SectionLabel(sectionType), sectionType + " frozen label");
                }
                Pass("C1 five-section-family COM-free compilation");

                JObject explicitFull = BuildSectionViewPlan(valid, "full_section");
                var explicitDefinition = (JObject)explicitFull["views"][1]
                    ["section_definition"];
                explicitDefinition["cutting_plane_mode"] = "explicit_full";
                explicitDefinition["cutting_line_points_model_m"] = new JArray(
                    new JArray(0.0125, -0.00632, -0.025),
                    new JArray(0.0125, 0.05752, -0.025));
                explicitDefinition["cutting_line_coordinate_space"] = "model";
                explicitDefinition["section_direction"] = new JArray(-1.0, 0.0, 0.0);
                explicitDefinition["cutting_line_axis"] = null;
                explicitDefinition["line_extension_ratio"] = null;
                Assert(validator.TryParse(explicitFull, out basicDocument, out error),
                    "explicit full section should satisfy ViewPlan 1.4: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "explicit full section should compile: " +
                    Format(executionError));
                AssertEqual(basicPlan.Views[1].SectionCuttingLineSource, "explicit_plan",
                    "explicit full-section source");
                Assert(basicPlan.Views[1].SectionPointsModel.Count == 2 &&
                    basicPlan.Views[1].SectionDirectionModel != null,
                    "explicit full-section geometry must remain frozen in the compiled plan");
                Pass("explicit full-section endpoints and direction compilation");

                JObject invalidDirection = (JObject)explicitFull.DeepClone();
                invalidDirection["views"][1]["section_definition"]["section_direction"] =
                    new JArray(0.0, 1.0, 0.0);
                Assert(validator.TryParse(invalidDirection, out basicDocument, out error),
                    "nonperpendicular explicit direction remains structurally valid");
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "nonperpendicular explicit direction must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_SECTION_DIRECTION_INVALID",
                    "explicit direction geometry code");
                Pass("explicit full-section direction rejection");

                JObject invalidHalf = BuildSectionViewPlan(valid, "half_section");
                Assert(validator.TryParse(invalidHalf, out basicDocument, out error),
                    "half-section baseline should satisfy Schema: " + Format(error));
                basicDocument.CanonicalPlan["views"][1]["section_definition"]
                    ["cutting_line_points_model_m"] = new JArray(
                        new JArray(0.0, 0.0, 0.0), new JArray(0.01, 0.0, 0.0));
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "half-section bad point count must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_SECTION_CONTRACT_INVALID",
                    "half-section point-count code");
                Pass("C1 section point-count rejection");

                invalidHalf = BuildSectionViewPlan(valid, "half_section");
                Assert(validator.TryParse(invalidHalf, out basicDocument, out error),
                    "half-section baseline should parse: " + Format(error));
                basicDocument.CanonicalPlan["views"][1]["section_definition"]
                    ["cutting_line_points_model_m"][2] = new JArray(0.02, 0.01, 0.0);
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "nonperpendicular half section must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_SECTION_GEOMETRY_INVALID",
                    "half-section perpendicularity code");
                Pass("C1 half-section geometry rejection");

                JObject invalidAligned = BuildSectionViewPlan(valid, "aligned_section");
                Assert(validator.TryParse(invalidAligned, out basicDocument, out error),
                    "aligned-section baseline should parse: " + Format(error));
                basicDocument.CanonicalPlan["views"][1]["section_definition"]
                    ["cutting_line_points_model_m"][2] = new JArray(0.05, 0.02, 0.0);
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "collinear aligned section must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_SECTION_GEOMETRY_INVALID",
                    "aligned-section collinearity code");
                Pass("C1 aligned-section geometry rejection");

                response = service.ValidatePartDrawingViewPlan(
                    new ToolRequest
                    {
                        OperationId = "contract-test-basic",
                        Tool = "validate_part_drawing_view_plan",
                        StateVersion = 7,
                        Params = PlanParameters(basicCandidate)
                    });
                result = response.ResultGeometry as JObject;
                AssertEqual(result.Value<string>("b2_basic_view_subset"), "compilable",
                    "private B2 subset assessment");
                AssertEqual(result.Value<string>("execution_readiness"), "supported",
                    "private B4 basic-view readiness");
                AssertEqual(result["b2_creation_order"][0].Value<string>(), "front",
                    "private B2 creation order");
                Assert(result.Value<bool>("solidworks_contacted") == false,
                    "B2 compilation must remain COM-free");
                Pass("private B2 execution-contract assessment");

                JObject namedCandidate = BuildBasicViewPlan(valid, false);
                var namedOrientation = (JObject)namedCandidate["views"][0]["orientation"];
                namedOrientation.RemoveAll();
                namedOrientation["kind"] = "named_model_view";
                namedOrientation["name_exact"] = "Manufacturing";
                namedOrientation["roll_angle_rad"] = 0.125;
                Assert(validator.TryParse(namedCandidate, out basicDocument, out error),
                    "named-view candidate should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                    "named-view candidate should compile: " + Format(executionError));
                AssertEqual(basicPlan.Views[0].OrientationKind, "named_model_view",
                    "named orientation kind");
                Assert(Math.Abs(basicPlan.Views[0].RollAngleRad - 0.125) < 1e-12,
                    "roll angle should be preserved");
                Pass("B2 exact named orientation and roll preservation");

                JObject centerElements = BuildBasicViewPlan(valid, false);
                centerElements["views"][0]["center_marks"] = valid["views"][0]["center_marks"].DeepClone();
                centerElements["views"][0]["symmetry_centerlines"] =
                    valid["views"][0]["symmetry_centerlines"].DeepClone();
                Assert(validator.TryParse(centerElements, out basicDocument, out error),
                    "center-element candidate should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "C4 center elements should compile before COM: " +
                    Format(executionError));
                Assert(basicPlan.Views[0].CenterMarks.Count == 1 &&
                    basicPlan.Views[0].CenterMarks[0].Style == 2 &&
                    basicPlan.Views[0].CenterMarks[0].ExpectedCount == 1 &&
                    basicPlan.Views[0].SymmetryCenterlines.Count == 2 &&
                    basicPlan.Views[0].SymmetryCenterlines[0].Axis == "horizontal" &&
                    basicPlan.Views[0].SymmetryCenterlines[1].Axis == "vertical",
                    "C4 center-element properties should be frozen");
                Pass("C4 center-element COM-free compilation");

                JObject detailCandidate = BuildDetailViewPlan(valid);
                Assert(validator.TryParse(detailCandidate, out basicDocument, out error),
                    "detail candidate should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                    "detail view should compile before COM: " + Format(executionError));
                ViewPlanBasicViewSpec detailSpec = basicPlan.Views[1];
                AssertEqual(detailSpec.Type, "detail_view", "C2 detail creation order");
                Assert(detailSpec.DetailStyle == 0 && detailSpec.DetailShowType == 0 &&
                    detailSpec.DetailShapeIntensity == 3 &&
                    Math.Abs(detailSpec.ProfileRadiusSheet - 0.01) < 1e-12,
                    "C2 detail properties should be frozen");
                Pass("C2 detail-view COM-free compilation");

                detailCandidate = BuildDetailViewPlan(valid);
                detailCandidate["views"][1]["label"] = new JObject
                {
                    ["text"] = "C-C",
                    ["show"] = true,
                    ["position_mode"] = "explicit",
                    ["position_sheet_m"] = new JArray(0.12, 0.18)
                };
                Assert(validator.TryParse(detailCandidate, out basicDocument, out error),
                    "explicit detail label should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "explicit detail label should compile before COM: " +
                    Format(executionError));
                detailSpec = basicPlan.Views[1];
                Assert(detailSpec.DetailLabelPositionMode == "explicit" &&
                    Math.Abs(detailSpec.DetailLabelX.Value - 0.12) < 1e-12 &&
                    Math.Abs(detailSpec.DetailLabelY.Value - 0.18) < 1e-12,
                    "C4 explicit detail-label position should be frozen");
                Pass("C4 explicit detail-label COM-free compilation");

                foreach (string style in new[] { "standard", "broken", "leader",
                    "no_leader", "connected" })
                    foreach (string showType in new[] { "profile", "circle", "none" })
                    {
                        detailCandidate = BuildDetailViewPlan(valid);
                        detailCandidate["views"][1]["detail_definition"]["style"] = style;
                        detailCandidate["views"][1]["detail_definition"]["show_type"] = showType;
                        Assert(validator.TryParse(detailCandidate, out basicDocument, out error),
                            "detail enum matrix should satisfy Schema: " + Format(error));
                        Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                            out executionError), "detail enum matrix should compile: " +
                            Format(executionError));
                    }
                Pass("C2 complete detail style/show-type mapping");

                detailCandidate = BuildDetailViewPlan(valid);
                detailCandidate["views"][1]["detail_definition"]
                    ["center_offset_from_parent_m"] = new JArray(0.06, 0.0);
                Assert(validator.TryParse(detailCandidate, out basicDocument, out error),
                    "out-of-box detail profile should satisfy Schema: " + Format(error));
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "detail profile outside parent box must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_DETAIL_PROFILE_INVALID",
                    "C2 detail profile containment code");
                Pass("C2 detail profile containment gate");

                JObject brokenCandidate = BuildBrokenOutViewPlan(valid);
                Assert(validator.TryParse(brokenCandidate, out basicDocument, out error),
                    "broken-out candidate should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                    "broken-out view should compile before COM: " + Format(executionError));
                ViewPlanBasicViewSpec brokenSpec = basicPlan.Views[1];
                AssertEqual(brokenSpec.Type, "broken_out_section", "C2 broken-out order");
                Assert(brokenSpec.ParentId == null && brokenSpec.OrientationKind ==
                    "standard_model_view" && Math.Abs(brokenSpec.BrokenOutDepth - 0.004) < 1e-12,
                    "C2 broken-out model-view semantics should be frozen");
                Pass("C2 broken-out-section COM-free compilation");

                brokenCandidate = BuildBrokenOutViewPlan(valid);
                Assert(validator.TryParse(brokenCandidate, out basicDocument, out error),
                    "broken-out reverse baseline should satisfy Schema: " + Format(error));
                basicDocument.CanonicalPlan["views"][1]["section_definition"]
                    ["reverse_direction"] = true;
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "unsupported broken-out reversal must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_BROKEN_OUT_CONTRACT_INVALID",
                    "C2 broken-out reversal code");
                Pass("C2 unsupported broken-out reversal rejection");

                JObject auxiliaryCandidate = BuildAuxiliaryViewPlan(valid);
                Assert(validator.TryParse(auxiliaryCandidate, out basicDocument, out error),
                    "auxiliary candidate should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "auxiliary view should compile before COM: " +
                    Format(executionError));
                ViewPlanBasicViewSpec auxiliarySpec = basicPlan.Views[1];
                AssertEqual(auxiliarySpec.Type, "auxiliary_view",
                    "C3 auxiliary creation order");
                Assert(!auxiliarySpec.AuxiliaryNotAligned &&
                    auxiliarySpec.AuxiliaryShowArrow && !auxiliarySpec.AuxiliaryFlip &&
                    Math.Abs(auxiliarySpec.AuxiliaryMatchToleranceSheet - 0.0001) < 1e-12 &&
                    auxiliarySpec.AuxiliaryReferenceEdgeStartModel.Length == 3 &&
                    auxiliarySpec.AuxiliaryReferenceEdgeEndModel.Length == 3,
                    "C3 auxiliary edge and creation properties should be frozen");
                Pass("C3 auxiliary-view COM-free compilation");

                auxiliaryCandidate = BuildAuxiliaryViewPlan(valid);
                auxiliaryCandidate["views"][1]["auxiliary_definition"]
                    ["reference_edge_end_model_m"] = new JArray(0.0, 0.0, 0.0);
                Assert(validator.TryParse(auxiliaryCandidate, out basicDocument, out error),
                    "degenerate auxiliary edge remains structurally valid: " + Format(error));
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "degenerate auxiliary edge must fail before COM");
                AssertEqual(executionError.Code,
                    "VIEW_PLAN_AUXILIARY_REFERENCE_EDGE_INVALID",
                    "C3 auxiliary degenerate-edge code");
                Pass("C3 auxiliary reference-edge rejection");

                auxiliaryCandidate = BuildAuxiliaryViewPlan(valid);
                auxiliaryCandidate["views"][1]["alignment"] = "not_aligned";
                Assert(validator.TryParse(auxiliaryCandidate, out basicDocument, out error),
                    "auxiliary alignment mismatch remains structurally valid: " + Format(error));
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "auxiliary alignment mismatch must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_AUXILIARY_ALIGNMENT_INVALID",
                    "C3 auxiliary alignment code");
                Pass("C3 auxiliary alignment consistency gate");

                auxiliaryCandidate = BuildAuxiliaryViewPlan(valid);
                auxiliaryCandidate["views"][1]["auxiliary_definition"]["show_arrow"] = false;
                Assert(validator.TryParse(auxiliaryCandidate, out basicDocument, out error),
                    "hidden auxiliary arrow remains structurally valid: " + Format(error));
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "hidden auxiliary arrow must fail before COM");
                AssertEqual(executionError.Code, "VIEW_PLAN_CAPABILITY_UNSUPPORTED",
                    "C3 hidden auxiliary-arrow capability code");
                Pass("C3 hidden auxiliary-arrow fail-closed gate");

                auxiliaryCandidate = BuildAuxiliaryViewPlan(valid);
                auxiliaryCandidate["views"][1]["label"] = new JObject
                {
                    ["text"] = "A",
                    ["show"] = true,
                    ["position_mode"] = "explicit",
                    ["position_sheet_m"] = new JArray(0.1, 0.1)
                };
                Assert(validator.TryParse(auxiliaryCandidate, out basicDocument, out error),
                    "explicit auxiliary label remains structurally valid: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                    out executionError), "explicit auxiliary label should compile before COM: " +
                    Format(executionError));
                auxiliarySpec = basicPlan.Views[1];
                AssertEqual(auxiliarySpec.AuxiliaryLabelPositionMode, "explicit",
                    "C4 explicit auxiliary-label mode");
                Assert(Math.Abs(auxiliarySpec.AuxiliaryLabelX.Value - 0.1) < 1e-12 &&
                    Math.Abs(auxiliarySpec.AuxiliaryLabelY.Value - 0.1) < 1e-12,
                    "C4 explicit auxiliary-label coordinates should be frozen");
                Pass("C4 explicit auxiliary-label COM-free compilation");

                JObject explicitBasis = BuildBasicViewPlan(valid, false);
                explicitBasis["execution_policy"]["transient_model_view_policy"] =
                    "allow_in_memory_restore";
                var explicitOrientation = (JObject)explicitBasis["views"][0]["orientation"];
                explicitOrientation.RemoveAll();
                explicitOrientation["kind"] = "explicit_basis";
                explicitOrientation["view_direction_model"] = new JArray(0.0, 0.0, -1.0);
                explicitOrientation["up_direction_model"] = new JArray(0.0, 1.0, 0.0);
                explicitOrientation["roll_angle_rad"] = 0.0;
                Assert(validator.TryParse(explicitBasis, out basicDocument, out error),
                    "explicit-basis candidate should satisfy Schema: " + Format(error));
                Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                    "explicit basis should compile under the transient restore policy: " +
                    Format(executionError));
                AssertEqual(basicPlan.Views[0].OrientationKind, "explicit_basis",
                    "explicit-basis orientation kind");
                Assert(Math.Abs(basicPlan.Views[0].ViewDirectionModel[2] + 1.0) < 1e-12 &&
                    Math.Abs(basicPlan.Views[0].UpDirectionModel[1] - 1.0) < 1e-12,
                    "explicit basis should be normalized and preserved");
                Pass("B2 explicit-basis compilation");

                JObject forbiddenExplicit = (JObject)explicitBasis.DeepClone();
                forbiddenExplicit["execution_policy"]["transient_model_view_policy"] = "forbid";
                Assert(validator.TryParse(forbiddenExplicit, out basicDocument, out error),
                    "forbidden explicit candidate remains structurally valid: " + Format(error));
                Assert(!basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                    "explicit basis must fail before COM when transient views are forbidden");
                AssertEqual(executionError.Code, "VIEW_PLAN_TRANSIENT_ORIENTATION_FORBIDDEN",
                    "explicit-basis policy code");
                AssertEqual(executionError.JsonPointer, "/views/0/orientation",
                    "explicit-basis policy pointer");
                Pass("B2 explicit-basis policy gate");

                string preflightRoot = Path.Combine(Path.GetTempPath(),
                    "q3ds-view-plan-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(preflightRoot);
                try
                {
                    JObject preflightCandidate = BuildPreflightPlan(valid, preflightRoot);
                    Assert(validator.TryParse(preflightCandidate, out basicDocument, out error),
                        "B3 preflight candidate should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                        "B3 preflight candidate should compile: " + Format(executionError));
                    var transactionPreflight = new ViewPlanBasicTransactionPreflight();
                    string outputPath = Path.Combine(preflightRoot, "result.SLDDRW");
                    ViewPlanBasicTransactionPaths transactionPaths;
                    Assert(transactionPreflight.TryValidate(basicPlan, outputPath,
                        out transactionPaths, out executionError),
                        "ten frozen artifacts should pass B3 preflight: " +
                        Format(executionError));
                    AssertEqual(transactionPaths.OutputPath, Path.GetFullPath(outputPath),
                        "normalized transaction output");
                    AssertEqual(transactionPaths.ReportPath,
                        Path.GetFullPath(outputPath) + ".verification.json",
                        "transaction verification sidecar");
                    Pass("B3 frozen-artifact preflight");

                    string geometryPath = preflightCandidate.Value<string>(
                        "geometry_report_path");
                    File.AppendAllText(geometryPath, "tampered", Encoding.UTF8);
                    Assert(!transactionPreflight.TryValidate(basicPlan, outputPath,
                        out transactionPaths, out executionError),
                        "changed frozen artifact must fail before COM");
                    AssertEqual(executionError.Code, "VIEW_PLAN_INPUT_HASH_MISMATCH",
                        "B3 integrity mismatch code");
                    Pass("B3 frozen-artifact hash mismatch rejection");

                    preflightCandidate = BuildPreflightPlan(valid, preflightRoot);
                    Assert(validator.TryParse(preflightCandidate, out basicDocument, out error),
                        "restored B3 candidate should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                        "restored B3 candidate should compile: " + Format(executionError));
                    File.WriteAllBytes(outputPath, new byte[] { 0x51, 0x33 });
                    Assert(!transactionPreflight.TryValidate(basicPlan, outputPath,
                        out transactionPaths, out executionError),
                        "an existing final artifact must never be overwritten");
                    AssertEqual(executionError.Code, "VIEW_PLAN_OUTPUT_EXISTS",
                        "B3 output no-overwrite code");
                    File.Delete(outputPath);
                    Pass("B3 existing output rejection");

                    Assert(!transactionPreflight.TryValidate(basicPlan, basicPlan.DrawingPath,
                        out transactionPaths, out executionError),
                        "output must differ from every frozen input");
                    AssertEqual(executionError.Code, "VIEW_PLAN_OUTPUT_PATH_INVALID",
                        "B3 input/output alias code");
                    Pass("B3 input/output alias rejection");

                    string originalLastPath = basicPlan.InputArtifacts[9].Path;
                    string originalLastHash = basicPlan.InputArtifacts[9].Sha256;
                    basicPlan.InputArtifacts[9].Path = basicPlan.InputArtifacts[8].Path;
                    basicPlan.InputArtifacts[9].Sha256 = basicPlan.InputArtifacts[8].Sha256;
                    Assert(!transactionPreflight.TryValidate(basicPlan, outputPath,
                        out transactionPaths, out executionError),
                        "two artifact roles must not bind the same path");
                    AssertEqual(executionError.Code, "VIEW_PLAN_INPUT_BINDING_INVALID",
                        "B3 distinct artifact-path code");
                    basicPlan.InputArtifacts[9].Path = originalLastPath;
                    basicPlan.InputArtifacts[9].Sha256 = originalLastHash;
                    Pass("B3 distinct artifact-path contract");

                    basicPlan.InputArtifacts[9].Role = "standard_view_image:invented";
                    Assert(!transactionPreflight.TryValidate(basicPlan, outputPath,
                        out transactionPaths, out executionError),
                        "ten artifacts with a substituted role must fail closed");
                    AssertEqual(executionError.Code, "VIEW_PLAN_INPUT_BINDING_INVALID",
                        "B3 required role code");
                    Pass("B3 exact artifact-role contract");

                    string sectionRoot = Path.Combine(preflightRoot, "c1-section");
                    Directory.CreateDirectory(sectionRoot);
                    string axisGeometry = "{\"features\":[{\"id\":\"B0F0\"," +
                        "\"surface_parameters\":{\"origin\":[0,0,0],\"axis\":[0,0,1]}}]}";
                    JObject sectionPreflight = BuildSectionPreflightPlan(valid, sectionRoot,
                        "full_section", axisGeometry);
                    Assert(validator.TryParse(sectionPreflight, out basicDocument, out error),
                        "full-section preflight should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                        out executionError), "full-section preflight should compile: " +
                        Format(executionError));
                    var geometryResolver = new ViewPlanSectionGeometryResolver();
                    Assert(geometryResolver.TryResolve(basicPlan, out executionError),
                        "unique full-section axis should resolve: " + Format(executionError));
                    Assert(basicPlan.Views[1].SectionFeatureAxisOriginsModel.Count == 1 &&
                        basicPlan.Views[1].SectionFeatureAxisDirectionsModel.Count == 1,
                        "full-section axis evidence should be frozen into the compiled plan");
                    Pass("C1 full-section frozen-axis resolution");

                    string explicitRoot = Path.Combine(preflightRoot, "c1-explicit-full");
                    Directory.CreateDirectory(explicitRoot);
                    sectionPreflight = BuildSectionPreflightPlan(valid, explicitRoot,
                        "full_section", "{\"features\":[{\"id\":\"B0F0\"}]}");
                    explicitDefinition = (JObject)sectionPreflight["views"][1]
                        ["section_definition"];
                    explicitDefinition["cutting_plane_mode"] = "explicit_full";
                    explicitDefinition["cutting_line_points_model_m"] = new JArray(
                        new JArray(0.0125, -0.00632, -0.025),
                        new JArray(0.0125, 0.05752, -0.025));
                    explicitDefinition["cutting_line_coordinate_space"] = "model";
                    explicitDefinition["section_direction"] = new JArray(-1.0, 0.0, 0.0);
                    explicitDefinition["cutting_line_axis"] = null;
                    explicitDefinition["line_extension_ratio"] = null;
                    Assert(validator.TryParse(sectionPreflight, out basicDocument, out error),
                        "explicit full-section preflight should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                        out executionError), "explicit full-section preflight should compile");
                    Assert(geometryResolver.TryResolve(basicPlan, out executionError),
                        "explicit endpoints must not require or derive feature axes: " +
                        Format(executionError));
                    Assert(basicPlan.Views[1].SectionFeatureAxisOriginsModel.Count == 0,
                        "explicit endpoints must not be overwritten by resolved axes");
                    Pass("explicit full-section bypasses axis derivation");

                    string ambiguousRoot = Path.Combine(preflightRoot, "c1-ambiguous");
                    Directory.CreateDirectory(ambiguousRoot);
                    sectionPreflight = BuildSectionPreflightPlan(valid, ambiguousRoot,
                        "full_section", "{\"features\":[{\"id\":\"B0F0\"},{\"id\":\"B0F0\"}]}");
                    Assert(validator.TryParse(sectionPreflight, out basicDocument, out error),
                        "ambiguous-axis plan should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                        out executionError), "ambiguous-axis plan should compile structurally");
                    Assert(!geometryResolver.TryResolve(basicPlan, out executionError),
                        "ambiguous geometry IDs must fail before COM");
                    AssertEqual(executionError.Code, "VIEW_PLAN_SECTION_FEATURE_AMBIGUOUS",
                        "C1 ambiguous feature code");
                    Pass("C1 ambiguous section-feature rejection");

                    string malformedRoot = Path.Combine(preflightRoot, "c1-malformed");
                    Directory.CreateDirectory(malformedRoot);
                    sectionPreflight = BuildSectionPreflightPlan(valid, malformedRoot,
                        "full_section", "{} {}");
                    Assert(validator.TryParse(sectionPreflight, out basicDocument, out error),
                        "malformed-geometry plan should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                        out executionError), "malformed-geometry plan should compile structurally");
                    Assert(!geometryResolver.TryResolve(basicPlan, out executionError),
                        "trailing geometry JSON must fail before COM");
                    AssertEqual(executionError.Code, "VIEW_PLAN_SECTION_GEOMETRY_INVALID",
                        "C1 malformed geometry code");
                    Pass("C1 malformed geometry-report rejection");

                    string offsetRoot = Path.Combine(preflightRoot, "c1-offset-miss");
                    Directory.CreateDirectory(offsetRoot);
                    sectionPreflight = BuildSectionPreflightPlan(valid, offsetRoot,
                        "offset_section", "{\"features\":[{\"id\":\"B0F0\"," +
                        "\"origin\":[0,0.03,0],\"axis\":[0,0,1]}]}");
                    Assert(validator.TryParse(sectionPreflight, out basicDocument, out error),
                        "offset-axis plan should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan,
                        out executionError), "offset-axis plan should compile structurally");
                    Assert(!geometryResolver.TryResolve(basicPlan, out executionError),
                        "offset path missing a feature axis must fail before COM");
                    AssertEqual(executionError.Code, "VIEW_PLAN_OFFSET_SECTION_AXIS_MISSED",
                        "C1 offset axis-miss code");
                    Pass("C1 offset-section feature-axis intersection gate");

                    preflightCandidate = BuildPreflightPlan(valid, preflightRoot);
                    Assert(validator.TryParse(preflightCandidate, out basicDocument, out error),
                        "B4 verification candidate should satisfy Schema: " + Format(error));
                    Assert(basicCompiler.TryCompile(basicDocument, out basicPlan, out executionError),
                        "B4 verification candidate should compile: " + Format(executionError));
                    File.WriteAllBytes(outputPath, new byte[] { 0x51, 0x33, 0x44, 0x53 });
                    JObject audit = BuildVerificationAudit(basicPlan, outputPath);
                    File.WriteAllText(outputPath + ".verification.json",
                        audit.ToString(Formatting.Indented), new UTF8Encoding(false));
                    var verificationPreflight = new ViewPlanBasicVerificationPreflight();
                    ViewPlanBasicVerificationInputs verificationInputs;
                    Assert(verificationPreflight.TryValidate(basicPlan, outputPath,
                        out verificationInputs, out executionError),
                        "a committed output and strict sidecar should pass B4 preflight: " +
                        Format(executionError));
                    AssertEqual(verificationInputs.ArtifactSha256, ComputeSha256(outputPath),
                        "B4 committed drawing hash");
                    Assert(verificationInputs.ExpectedHandles.Count == basicPlan.Views.Count,
                        "B4 expected handle inventory");
                    Pass("B4 committed-drawing verification preflight");

                    File.AppendAllText(outputPath, "tampered", Encoding.UTF8);
                    Assert(!verificationPreflight.TryValidate(basicPlan, outputPath,
                        out verificationInputs, out executionError),
                        "a changed committed drawing must fail before COM");
                    AssertEqual(executionError.Code, "VIEW_PLAN_OUTPUT_HASH_MISMATCH",
                        "B4 committed drawing hash mismatch code");
                    Pass("B4 committed-drawing hash mismatch rejection");

                    File.WriteAllBytes(outputPath, new byte[] { 0x51, 0x33, 0x44, 0x53 });
                    audit = BuildVerificationAudit(basicPlan, outputPath);
                    audit["plan_canonical_sha256"] = new string('0', 64);
                    File.WriteAllText(outputPath + ".verification.json",
                        audit.ToString(Formatting.Indented), new UTF8Encoding(false));
                    Assert(!verificationPreflight.TryValidate(basicPlan, outputPath,
                        out verificationInputs, out executionError),
                        "a sidecar bound to another plan must fail before COM");
                    AssertEqual(executionError.Code, "VIEW_PLAN_VERIFICATION_REPORT_MISMATCH",
                        "B4 sidecar plan mismatch code");
                    Pass("B4 sidecar binding rejection");
                }
                finally
                {
                    Directory.Delete(preflightRoot, true);
                }

                response = service.ValidatePartDrawingViewPlan(
                    new ToolRequest
                    {
                        OperationId = "contract-test-2",
                        Tool = "validate_part_drawing_view_plan",
                        StateVersion = 6,
                        Params = PlanParameters(valid)
                    });
                AssertEqual(response.Error.Code, "INVALID_STATE_VERSION",
                    "private service state guard");
                Pass("private service state-version guard");

                Console.WriteLine(_passed + "/" + _passed + " ViewPlan contract tests passed");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("FAILED: " + ex.Message);
                Console.Error.WriteLine(ex);
                return 1;
            }
        }

        private static void AssertInvalid(ViewPlanContractValidator validator,
            JObject candidate, string expectedPointer)
        {
            ViewPlanDocument document;
            ViewPlanContractError error;
            Assert(!validator.TryParse(candidate, out document, out error),
                "candidate should be rejected");
            Assert(error != null, "rejected candidate requires an error");
            AssertEqual(error.Code, "VIEW_PLAN_SCHEMA_INVALID", "stable validation code");
            AssertEqual(error.JsonPointer, expectedPointer, "validation JSON pointer");
        }

        private static JObject ReverseProperties(JObject source)
        {
            var result = new JObject();
            foreach (JProperty property in source.Properties().Reverse())
                result[property.Name] = property.Value.DeepClone();
            return result;
        }

        private static JObject PlanParameters(JObject plan)
        {
            var parameters = new JObject();
            parameters["plan"] = plan.DeepClone();
            return parameters;
        }

        private static JObject BuildBasicViewPlan(JObject source, bool childFirst)
        {
            var plan = (JObject)source.DeepClone();
            var parent = (JObject)plan["views"][0];
            parent["center_marks"] = new JArray();
            parent["symmetry_centerlines"] = new JArray();

            var child = (JObject)parent.DeepClone();
            child["id"] = "projected-right";
            child["type"] = "projected_view";
            child["source"] = new JObject
            {
                ["kind"] = "parent_view",
                ["reference"] = "front",
                ["projection_direction"] = "right"
            };
            child["orientation"] = new JObject { ["kind"] = "derived_from_parent" };
            child["parent_view_id"] = "front";
            child["alignment"] = "horizontal";
            // First-angle right projection is placed to the left of its parent.
            child["position_sheet_m"] = new JArray(0.025, 0.165);
            child["placement_box"] = new JObject
            {
                ["x_min_m"] = 0.005,
                ["y_min_m"] = 0.105,
                ["x_max_m"] = 0.045,
                ["y_max_m"] = 0.225
            };
            plan["views"] = childFirst
                ? new JArray(child, parent)
                : new JArray(parent, child);
            return plan;
        }

        private static JObject BuildSectionViewPlan(JObject source, string sectionType)
        {
            var plan = (JObject)source.DeepClone();
            var parent = (JObject)plan["views"][0];
            parent["center_marks"] = new JArray();
            parent["symmetry_centerlines"] = new JArray();
            var section = (JObject)plan["views"][1];
            section["id"] = "section-c1";
            section["type"] = sectionType;
            section["center_marks"] = new JArray();
            section["symmetry_centerlines"] = new JArray();
            section["alignment"] = sectionType == "full_section"
                ? "projected" : "not_aligned";
            section["label"]["text"] = SectionLabel(sectionType);
            section["section_definition"] = SectionDefinition(sectionType);
            plan["views"] = new JArray(parent, section);
            foreach (JObject requirement in plan["feature_coverage"].Children<JObject>()
                .SelectMany(item => item["requirements"].Children<JObject>()))
                if (requirement.Value<string>("satisfied_by") == "section_A_A")
                    requirement["satisfied_by"] = "section-c1";
            foreach (JObject row in plan["decision_summary"]["final_minimum_view_set"]
                .Children<JObject>())
                if (row.Value<string>("view_id") == "section_A_A")
                    row["view_id"] = "section-c1";
            foreach (JObject zone in plan["dimension_zones"].Children<JObject>())
                if (zone.Value<string>("view_id") == "section_A_A")
                    zone["view_id"] = "section-c1";
            return plan;
        }

        private static JObject SectionDefinition(string sectionType)
        {
            var result = new JObject
            {
                ["feature_ids"] = new JArray("B0F0"),
                ["reverse_direction"] = false,
                ["section_depth_m"] = 0
            };
            if (sectionType == "full_section")
            {
                result["cutting_plane_mode"] = "through_feature_axes";
                result["cutting_line_points_model_m"] = new JArray();
                result["cutting_line_axis"] = "vertical";
                result["line_extension_ratio"] = 0.1;
            }
            else
            {
                result["cutting_plane_mode"] = sectionType == "half_section"
                    ? "explicit_half" : sectionType == "offset_section"
                    ? "explicit_offset" : sectionType == "aligned_section"
                    ? "explicit_aligned" : "explicit_removed";
                result["cutting_line_axis"] = null;
                result["line_extension_ratio"] = null;
                result["cutting_line_points_model_m"] = sectionType == "half_section"
                    ? new JArray(new JArray(-0.05, 0.0, 0.0),
                        new JArray(0.0, 0.0, 0.0), new JArray(0.0, 0.05, 0.0))
                    : sectionType == "offset_section"
                    ? new JArray(new JArray(-0.05, 0.0, 0.0),
                        new JArray(0.0, 0.0, 0.0), new JArray(0.0, 0.01, 0.0),
                        new JArray(0.05, 0.01, 0.0))
                    : sectionType == "aligned_section"
                    ? new JArray(new JArray(-0.05, -0.02, 0.0),
                        new JArray(0.0, 0.0, 0.0), new JArray(0.05, -0.02, 0.0))
                    : new JArray(new JArray(-0.05, 0.0, 0.0),
                        new JArray(0.05, 0.0, 0.0));
            }
            return result;
        }

        private static string SectionLabel(string sectionType)
        {
            return sectionType.Replace("_section", "").ToUpperInvariant() + "-" +
                sectionType.Replace("_section", "").ToUpperInvariant();
        }

        private static JObject BuildDetailViewPlan(JObject source)
        {
            var plan = BuildSectionViewPlan(source, "removed_section");
            var detail = (JObject)plan["views"][1];
            detail["type"] = "detail_view";
            detail["alignment"] = "none";
            detail["section_definition"] = null;
            detail["detail_definition"] = new JObject
            {
                ["profile_mode"] = "circle",
                ["center_offset_from_parent_m"] = new JArray(0.0, 0.0),
                ["radius_sheet_m"] = 0.01,
                ["style"] = "standard",
                ["show_type"] = "profile",
                ["full_outline"] = true,
                ["jagged_outline"] = false,
                ["no_outline"] = false,
                ["shape_intensity"] = 3
            };
            return plan;
        }

        private static JObject BuildBrokenOutViewPlan(JObject source)
        {
            var plan = (JObject)source.DeepClone();
            var parent = (JObject)plan["views"][0];
            parent["center_marks"] = new JArray();
            parent["symmetry_centerlines"] = new JArray();
            var template = (JObject)plan["views"][1];
            var broken = (JObject)parent.DeepClone();
            broken["id"] = "broken-c2";
            broken["type"] = "broken_out_section";
            broken["position_sheet_m"] = template["position_sheet_m"].DeepClone();
            broken["placement_box"] = template["placement_box"].DeepClone();
            broken["parent_view_id"] = null;
            broken["alignment"] = "none";
            broken["section_definition"] = new JObject
            {
                ["cutting_plane_mode"] = "explicit_broken_out",
                ["feature_ids"] = new JArray("B0F0"),
                ["cutting_line_points_model_m"] = new JArray(),
                ["cutting_line_axis"] = null,
                ["line_extension_ratio"] = null,
                ["reverse_direction"] = false,
                ["section_depth_m"] = 0
            };
            broken["broken_out_definition"] = new JObject
            {
                ["base_view_mode"] = "model_orientation",
                ["boundary_mode"] = "circle",
                ["center_offset_from_view_m"] = new JArray(0.0, 0.0),
                ["radius_sheet_m"] = 0.01,
                ["depth_m"] = 0.004
            };
            broken["detail_definition"] = null;
            broken["auxiliary_definition"] = null;
            broken["label"] = null;
            plan["views"] = new JArray(parent, broken);
            return plan;
        }

        private static JObject BuildAuxiliaryViewPlan(JObject source)
        {
            var plan = BuildSectionViewPlan(source, "removed_section");
            var auxiliary = (JObject)plan["views"][1];
            auxiliary["id"] = "auxiliary-c3";
            auxiliary["type"] = "auxiliary_view";
            auxiliary["alignment"] = "projected";
            auxiliary["section_definition"] = null;
            auxiliary["detail_definition"] = null;
            auxiliary["auxiliary_definition"] = new JObject
            {
                ["reference_edge_start_model_m"] = new JArray(0.0, 0.0, 0.0),
                ["reference_edge_end_model_m"] = new JArray(0.1, 0.0, 0.0),
                ["match_tolerance_sheet_m"] = 0.0001,
                ["not_aligned"] = false,
                ["show_arrow"] = true,
                ["flip"] = false
            };
            auxiliary["label"]["text"] = "A";
            foreach (JObject row in plan["decision_summary"]["final_minimum_view_set"]
                .Children<JObject>())
                if (row.Value<string>("view_id") == "section-c1")
                    row["view_id"] = "auxiliary-c3";
            foreach (JObject zone in plan["dimension_zones"].Children<JObject>())
                if (zone.Value<string>("view_id") == "section-c1")
                    zone["view_id"] = "auxiliary-c3";
            return plan;
        }

        private static JObject BuildPreflightPlan(JObject source, string directory)
        {
            return BindPreflightArtifacts(BuildBasicViewPlan(source, false), directory, "{}");
        }

        private static JObject BuildSectionPreflightPlan(JObject source, string directory,
            string sectionType, string geometryJson)
        {
            return BindPreflightArtifacts(BuildSectionViewPlan(source, sectionType), directory,
                geometryJson);
        }

        private static JObject BindPreflightArtifacts(JObject plan, string directory,
            string geometryJson)
        {
            string modelPath = WriteFixtureArtifact(directory, "part.SLDPRT", "model");
            string drawingPath = WriteFixtureArtifact(directory, "blank.SLDDRW", "drawing");
            string geometryPath = WriteFixtureArtifact(directory, "model-geometry.json",
                geometryJson);
            string readinessPath = WriteFixtureArtifact(directory, "drawing-readiness.json", "{}");
            plan["model_path"] = modelPath;
            plan["model_sha256"] = ComputeSha256(modelPath);
            plan["drawing_path"] = drawingPath;
            plan["drawing_sha256"] = ComputeSha256(drawingPath);
            plan["geometry_report_path"] = geometryPath;
            plan["geometry_report_sha256"] = ComputeSha256(geometryPath);
            plan["readiness_report_path"] = readinessPath;
            plan["readiness_report_sha256"] = ComputeSha256(readinessPath);
            foreach (JObject image in ((JArray)plan["standard_view_images"]).OfType<JObject>())
            {
                string view = image.Value<string>("view");
                string imagePath = WriteFixtureArtifact(directory, view + ".png", "png:" + view);
                image["path"] = imagePath;
                image["sha256"] = ComputeSha256(imagePath);
            }
            return plan;
        }

        private static string WriteFixtureArtifact(string directory, string name, string content)
        {
            string path = Path.Combine(directory, name);
            File.WriteAllText(path, content, new UTF8Encoding(false));
            return Path.GetFullPath(path);
        }

        private static string ComputeSha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "")
                    .ToLowerInvariant();
        }

        private static JObject BuildVerificationAudit(ViewPlanBasicExecutionPlan plan,
            string outputPath)
        {
            var handles = new JObject();
            foreach (ViewPlanBasicViewSpec view in plan.Views)
                handles[view.Id] = "persistent-" + view.Id;
            return new JObject
            {
                ["schema_version"] = "1.0",
                ["operation_id"] = "contract-b4-verification",
                ["generated_at_utc"] = DateTime.UtcNow.ToString("o",
                    CultureInfo.InvariantCulture),
                ["plan_id"] = plan.PlanId,
                ["plan_canonical_sha256"] = plan.PlanCanonicalSha256,
                ["artifact_sha256"] = ComputeSha256(outputPath),
                ["output_path"] = Path.GetFullPath(outputPath),
                ["verified"] = true,
                ["input_artifacts"] = new JArray(plan.InputArtifacts.Select(item =>
                    new JObject
                    {
                        ["role"] = item.Role,
                        ["path"] = item.Path,
                        ["sha256"] = item.Sha256
                    })),
                ["view_handles"] = handles,
                ["verification"] = new JObject
                {
                    ["verified"] = true,
                    ["view_count"] = plan.Views.Count,
                    ["views"] = new JArray()
                }
            };
        }

        private static JObject ReadJsonObjectWithoutDateCoercion(string path)
        {
            using (var stream = File.OpenText(path))
            using (var reader = new JsonTextReader(stream))
            {
                reader.DateParseHandling = DateParseHandling.None;
                return JObject.Load(reader, new JsonLoadSettings
                {
                    DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                    CommentHandling = CommentHandling.Ignore,
                    LineInfoHandling = LineInfoHandling.Ignore
                });
            }
        }

        private static string Format(ViewPlanContractError error)
        {
            return error == null ? "<none>" : error.Code + " " + error.JsonPointer + " " + error.Message;
        }

        private static string Format(ViewPlanExecutionContractError error)
        {
            return error == null ? "<none>" : error.Code + " " + error.JsonPointer + " " + error.Message;
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }

        private static void AssertEqual(string actual, string expected, string label)
        {
            if (!string.Equals(actual, expected, StringComparison.Ordinal))
                throw new InvalidOperationException(label + ": expected='" + expected +
                    "' actual='" + actual + "'");
        }

        private static void Pass(string name)
        {
            _passed++;
            Console.WriteLine("ok - " + name);
        }

        private sealed class TestGuard : IOperationGuard
        {
            private readonly int _stateVersion;

            public TestGuard(int stateVersion)
            {
                _stateVersion = stateVersion;
            }

            public bool IsDuplicate(string operationId) { return false; }
            public ExecutionResponse GetDuplicate(string operationId) { return null; }
            public bool IsStateVersionValid(int incomingStateVersion)
            {
                return incomingStateVersion == _stateVersion;
            }
            public void RegisterCompleted(string operationId, ExecutionResponse response)
            {
                throw new InvalidOperationException("read-only contract validation cannot commit");
            }
            public int GetCurrentStateVersion() { return _stateVersion; }
        }
    }
}
