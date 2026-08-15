using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace DrawingLayoutContractTests
{
    internal static class Program
    {
        private static int _passed;
        private static string _repo;

        private static int Main(string[] args)
        {
            try
            {
                _repo = Path.GetFullPath(args[0]);
                string schema = Path.Combine(_repo, "drawing_layout_planner", "contracts",
                    "drawing-layout-plan.schema.json");
                var validator = new DrawingLayoutPlanContractValidator(schema);
                string root = NewRoot();
                JObject candidate = BuildFixture(root);
                DrawingLayoutPlanDocument document; DrawingLayoutPlanContractError error;
                Assert(validator.TryParse(candidate, out document, out error), Format(error));
                Pass("strict DrawingLayoutPlan 1.0 accepted");

                DrawingLayoutExecutionPlan plan;
                Assert(new DrawingLayoutPlanExecutionCompiler().TryCompile(document,
                    out plan, out error), Format(error));
                Assert(plan.Operations.Count == 8 && plan.Operations.Last().Phase == 5,
                    "complete operation union was not compiled");
                Pass("eight native operations compile in frozen phase order");

                JObject unknown = (JObject)candidate.DeepClone(); unknown["legacy_layout"] = true;
                Assert(!validator.TryParse(unknown, out document, out error),
                    "unknown field accepted");
                Pass("unknown plan fields rejected");

                JObject weakened = (JObject)candidate.DeepClone();
                weakened["execution_policy"]["allow_partial_commit"] = true;
                Assert(!validator.TryParse(weakened, out document, out error),
                    "weakened atomicity accepted");
                Pass("execution policy cannot be weakened");

                JObject gap = (JObject)candidate.DeepClone();
                gap["operations"][7]["sequence"] = 9;
                Assert(validator.TryParse(gap, out document, out error), Format(error));
                Assert(!new DrawingLayoutPlanExecutionCompiler().TryCompile(document,
                    out plan, out error) && error.Code == "DRAWING_LAYOUT_PLAN_SEQUENCE_INVALID",
                    "sequence gap accepted");
                Pass("operation sequence gaps rejected");

                JObject phase = (JObject)candidate.DeepClone();
                JToken first = phase["operations"][0]; phase["operations"][0] =
                    phase["operations"][4]; phase["operations"][4] = first;
                for (int i = 0; i < 8; i++) phase["operations"][i]["sequence"] = i;
                Assert(validator.TryParse(phase, out document, out error), Format(error));
                Assert(!new DrawingLayoutPlanExecutionCompiler().TryCompile(document,
                    out plan, out error) && error.Code == "DRAWING_LAYOUT_PLAN_PHASE_INVALID",
                    "phase regression accepted");
                Pass("six-stage order is fail-closed");

                JObject locked = (JObject)candidate.DeepClone();
                locked["source_invariants"]["locked_object_ids"] = new JArray("dim-object");
                Assert(validator.TryParse(locked, out document, out error), Format(error));
                Assert(!new DrawingLayoutPlanExecutionCompiler().TryCompile(document,
                    out plan, out error) && error.Code == "DRAWING_LAYOUT_OPERATION_LOCKED",
                    "locked object move accepted");
                Pass("frozen object movement rejected");

                Assert(validator.TryParse(candidate, out document, out error), Format(error));
                Assert(new DrawingLayoutPlanExecutionCompiler().TryCompile(document,
                    out plan, out error), Format(error));
                string planPath = Path.Combine(root, "drawing_layout_plan.json");
                File.WriteAllText(planPath, candidate.ToString());
                string planFileHash = DrawingLayoutPlanContractValidator.FileSha256(planPath);
                string outputPath = Path.Combine(root, "final.SLDDRW");
                DrawingLayoutTransactionPaths paths;
                Assert(new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                    planFileHash, outputPath, out paths, out error), Format(error));
                Pass("immutable G1/DimensionPlan/drawing/sidecar chain accepted");

                Assert(!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                    new string('0', 64), outputPath, out paths, out error) &&
                    error.Code == "DRAWING_LAYOUT_PLAN_INPUT_HASH_MISMATCH",
                    "plan hash drift accepted");
                Pass("published plan hash drift rejected");

                File.WriteAllBytes(outputPath, new byte[] { 9 });
                Assert(!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                    planFileHash, outputPath, out paths, out error) &&
                    error.Code == "DRAWING_LAYOUT_OUTPUT_EXISTS", "output overwrite accepted");
                File.Delete(outputPath);
                Pass("upstream/final drawing overwrite rejected");

                string production = Path.Combine(_repo, "drawing_layout_planner", "capabilities",
                    "plan-current.json");
                string boundary = Path.Combine(_repo, "drawing_layout_planner", "capabilities",
                    "current.json");
                Assert(new DrawingLayoutPlanCapabilityPreflight().TryValidate(plan, production,
                    boundary, out error), Format(error));
                Pass("G7-promoted production capabilities are executable");

                Assert(new DrawingLayoutPlanCapabilityPreflight().TryValidateQualification(
                    plan, production, boundary, out error), Format(error));
                Pass("G7 qualification accepts the all-live production registries");

                string supportedBoundary, supportedPlan;
                BuildSupportedRegistries(root, out supportedPlan, out supportedBoundary);
                Assert(new DrawingLayoutPlanCapabilityPreflight().TryValidate(plan,
                    supportedPlan, supportedBoundary, out error), Format(error));
                Pass("hash-bound all-live capability fixture accepted");

                JObject plannedRegistry = JObject.Parse(File.ReadAllText(supportedPlan));
                foreach (JProperty entry in ((JObject)plannedRegistry["operations"]).Properties())
                    entry.Value["status"] = "planned";
                foreach (JProperty entry in ((JObject)plannedRegistry["safety_elements"]).Properties())
                    entry.Value["status"] = "planned";
                string plannedRegistryPath = Path.Combine(root,
                    "planned-layout-plan-capabilities.json");
                File.WriteAllText(plannedRegistryPath, plannedRegistry.ToString());
                Assert(new DrawingLayoutPlanCapabilityPreflight().TryValidateQualification(plan,
                    plannedRegistryPath, supportedBoundary, out error), Format(error));
                Pass("G7 qualification admits planned operations only with supported boundaries");

                string matrixPath = Path.Combine(root, "drawing-layout-g7-matrix-request.json");
                string requestHash = new string('b', 64);
                string sourceRequestHash = new string('c', 64);
                File.WriteAllText(matrixPath, new JObject {
                    ["protocol_id"]="solidworks-drawing-layout-g7-matrix-request",
                    ["schema_version"]="1.0", ["solidworks_revision"]="33.5.0",
                    ["positive_cases"] = new JArray(new JObject {
                        ["case_id"]="G7-contract", ["plan_path"] = planPath,
                        ["plan_file_sha256"] = planFileHash,
                        ["plan_canonical_sha256"] = plan.PlanSha256,
                        ["planning_request_sha256"] = requestHash,
                        ["source_dimension_request_sha256"] = sourceRequestHash,
                        ["output_path"] = outputPath }) }.ToString());
                string matrixHash = DrawingLayoutPlanContractValidator.FileSha256(matrixPath);
                Assert(new DrawingLayoutPlanQualificationPreflight().TryValidate(plan, planPath,
                    planFileHash, outputPath, matrixPath, matrixHash, requestHash,
                    sourceRequestHash, "G7-contract", out error), Format(error));
                Pass("G7 qualification preflight binds immutable matrix case");
                Assert(!new DrawingLayoutPlanQualificationPreflight().TryValidate(plan, planPath,
                    planFileHash, outputPath, matrixPath, matrixHash, requestHash,
                    new string('d', 64), "G7-contract", out error) &&
                    error.Code == "DRAWING_LAYOUT_G7_CASE_BINDING_MISMATCH",
                    "G7 matrix source request binding drift accepted");
                Pass("G7 qualification preflight rejects nested request drift");

                JObject unsupported = JObject.Parse(File.ReadAllText(supportedBoundary));
                ((JArray)unsupported["capabilities"])[1]["status"] = "unsupported";
                File.WriteAllText(supportedBoundary, unsupported.ToString());
                JObject registry = JObject.Parse(File.ReadAllText(supportedPlan));
                registry["boundary_registry"]["manifest_sha256"] =
                    DrawingLayoutPlanContractValidator.FileSha256(supportedBoundary);
                File.WriteAllText(supportedPlan, registry.ToString());
                Assert(!new DrawingLayoutPlanCapabilityPreflight().TryValidate(plan,
                    supportedPlan, supportedBoundary, out error) &&
                    error.Code == "DRAWING_LAYOUT_BOUNDARY_CAPABILITY_BLOCKED",
                    "unsupported exact boundary accepted");
                Pass("required G0 boundary remains independently blocking");

                string finalPath = WriteCommittedG4(root, plan, planPath, planFileHash);
                string dimensionSchema = Path.Combine(_repo, "dimension_planner", "contracts",
                    "dimension-plan.schema.json");
                string verificationSchema = Path.Combine(_repo, "drawing_layout_planner",
                    "contracts", "drawing-layout-verification.schema.json");
                DrawingLayoutVerificationInputs verificationInputs;
                Assert(new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                    planFileHash, finalPath, dimensionSchema, verificationSchema,
                    out verificationInputs, out error), Format(error));
                Assert(verificationInputs.DimensionPlan.Dimensions.Single().PositionX == 0.12 &&
                    verificationInputs.DimensionPlan.Dimensions.Single().PositionY == 0.15,
                    "authorized G4 position was not overlaid on DimensionPlan semantics");
                Pass("G5 recursively bound committed drawing and strict G4 sidecar");
                Pass("G5 preserves DimensionPlan semantics with authorized layout position");

                string layoutSidecar = finalPath + ".layout-verification.json";
                JObject mismatchedFingerprint = JObject.Parse(File.ReadAllText(layoutSidecar));
                mismatchedFingerprint["reopen_verification"]["layout_fingerprint_sha256"] =
                    new string('e', 64);
                File.WriteAllText(layoutSidecar, mismatchedFingerprint.ToString());
                Assert(!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                    planFileHash, finalPath, dimensionSchema, verificationSchema,
                    out verificationInputs, out error) &&
                    error.Code == "DRAWING_LAYOUT_VERIFICATION_REPORT_INVALID",
                    "three-stage fingerprint drift accepted");
                Pass("G5 rejects G4 in-memory/reopen fingerprint drift");

                finalPath = WriteCommittedG4(root, plan, planPath, planFileHash);
                layoutSidecar = finalPath + ".layout-verification.json";
                JObject unknownSidecar = JObject.Parse(File.ReadAllText(layoutSidecar));
                unknownSidecar["trusted_without_readback"] = true;
                File.WriteAllText(layoutSidecar, unknownSidecar.ToString());
                Assert(!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                    planFileHash, finalPath, dimensionSchema, verificationSchema,
                    out verificationInputs, out error) &&
                    error.Code == "DRAWING_LAYOUT_VERIFICATION_REPORT_INVALID",
                    "unknown G4 sidecar field accepted");
                Pass("G5 validates the strict hash-locked sidecar schema");

                finalPath = WriteCommittedG4(root, plan, planPath, planFileHash);
                File.AppendAllText(finalPath, "drift");
                Assert(!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                    planFileHash, finalPath, dimensionSchema, verificationSchema,
                    out verificationInputs, out error) &&
                    error.Code == "DRAWING_LAYOUT_OUTPUT_HASH_MISMATCH",
                    "final drawing hash drift accepted");
                Pass("G5 rejects committed final drawing hash drift");

                File.Delete(layoutSidecar);
                File.Delete(finalPath);

                Console.WriteLine("Drawing layout G4/G5/G7 contract tests passed: " + _passed + "/22");
                return 0;
            }
            catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
        }

        private static JObject BuildFixture(string root)
        {
            string dimensionPlanPath = Path.Combine(root, "dimension_plan.json");
            string drawingPath = Path.Combine(root, "dimensioned.SLDDRW");
            string sidecarPath = Path.Combine(root, "dimension-verification.json");
            string handoffPath = Path.Combine(root, "drawing-layout-handoff.json");
            string dimensionHandoff = Path.Combine(root, "dimension-upstream-handoff.json");
            string model = Path.Combine(root, "dimension-upstream-source_model.SLDPRT");
            string viewDrawing = Path.Combine(root, "dimension-upstream-source_drawing.SLDDRW");
            string viewPlan = Path.Combine(root, "dimension-upstream-view_plan.json");
            string viewSidecar = Path.Combine(root, "dimension-upstream-verification_sidecar.json");
            File.WriteAllBytes(model, new byte[] { 21 });
            File.WriteAllBytes(viewDrawing, new byte[] { 22 });
            File.WriteAllText(viewPlan, "{}"); File.WriteAllText(viewSidecar, "{}");
            string modelHash = DrawingLayoutPlanContractValidator.FileSha256(model);
            string viewDrawingHash = DrawingLayoutPlanContractValidator.FileSha256(viewDrawing);
            string viewPlanHash = DrawingLayoutPlanContractValidator.FileSha256(viewPlan);
            string viewSidecarHash = DrawingLayoutPlanContractValidator.FileSha256(viewSidecar);
            File.WriteAllText(dimensionHandoff, new JObject
            {
                ["protocol_id"] = "solidworks-dimension-planning-handoff",
                ["schema_version"] = "1.0", ["handoff_id"] = "DMH-G5", ["status"] = "ready",
                ["source_model"] = new JObject { ["path"] = model, ["sha256"] = modelHash,
                    ["configuration"] = "Default", ["save_flag"] = false },
                ["drawing_context"] = new JObject { ["path"] = viewDrawing },
                ["upstream_artifacts"] = new JArray(
                    Ledger("source_model", model, modelHash),
                    Ledger("verified_drawing", viewDrawing, viewDrawingHash),
                    Ledger("view_plan", viewPlan, viewPlanHash),
                    Ledger("verification_sidecar", viewSidecar, viewSidecarHash)),
                ["views"] = new JArray(new JObject { ["view_id"] = "front",
                    ["solidworks_name"] = "Front", ["projected_geometry"] = new JArray(
                        Geometry("GE-1", "AQID", new JArray(0.1,0.1,0.2,0.1)),
                        Geometry("GE-2", "BAUG", new JArray(0.1,0.2,0.2,0.2))) }),
                ["model_driven_dimensions"] = new JArray(new JObject {
                    ["dimension_id"] = "MD-1", ["full_name"] = "D1@Sketch1",
                    ["value_si"] = 0.01 }),
                ["manufacturing_features"] = new JArray(new JObject {
                    ["feature_id"] = "MF-1", ["classification"] = "hole" }),
                ["approved_user_inputs"] = new JArray(),
                ["reference_measurements"] = new JArray()
            }.ToString());
            string dimensionHandoffHash = DrawingLayoutPlanContractValidator.FileSha256(
                dimensionHandoff);
            string planContractHash = new string('b', 64);
            JObject dimensionPlanValue = new JObject
            {
                ["$schema"] = "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json",
                ["protocol_id"] = "solidworks-dimension-plan", ["schema_version"] = "1.0",
                ["plan_id"] = "DP-G4", ["created_at_utc"] = "2026-08-15T08:00:00Z",
                ["producer"] = new JObject { ["name"] = "contract-test", ["version"] = "1.0.0",
                    ["ruleset_id"] = "g5", ["ruleset_sha256"] = planContractHash },
                ["execution_policy"] = new JObject { ["on_integrity_mismatch"] = "fail",
                    ["on_selection_ambiguity"] = "fail", ["on_unsupported_dimension"] = "fail",
                    ["on_layout_violation"] = "fail", ["allow_source_model_write"] = false,
                    ["allow_upstream_drawing_overwrite"] = false, ["allow_partial_commit"] = false },
                ["handoff"] = Artifact(dimensionHandoff, dimensionHandoffHash),
                ["handoff_id"] = "DMH-G5", ["source_model"] = Artifact(model, modelHash),
                ["source_drawing"] = Artifact(viewDrawing, viewDrawingHash),
                ["view_plan"] = Artifact(viewPlan, viewPlanHash),
                ["verification_sidecar"] = Artifact(viewSidecar, viewSidecarHash),
                ["configuration"] = "Default", ["dimensions"] = new JArray(new JObject {
                    ["dimension_id"] = "D-1", ["kind"] = "linear",
                    ["source"] = new JObject { ["source_tier"] = "model_or_pmi",
                        ["handoff_collection"] = "model_driven_dimensions",
                        ["source_ids"] = new JArray("MD-1") }, ["target_view_id"] = "front",
                    ["attachments"] = new JArray(
                        Attachment("A-1", "GE-1", "AQID", "first"),
                        Attachment("A-2", "GE-2", "BAUG", "second")),
                    ["feature_ids"] = new JArray("MF-1"), ["value"] = new JObject {
                        ["value_mode"] = "model_driven", ["quantity_kind"] = "length",
                        ["nominal_si"] = 0.01 }, ["tolerance"] = JValue.CreateNull(),
                    ["display_format"] = new JObject { ["unit"] = "mm", ["precision"] = 2,
                        ["prefix"] = "", ["suffix"] = "", ["show_parentheses"] = false,
                        ["show_units"] = false, ["dual_units"] = false },
                    ["dimension_zone_id"] = "DZ-front-top", ["hierarchy"] = new JObject {
                        ["level"] = "manufacturing", ["priority"] = 1,
                        ["chain_id"] = JValue.CreateNull(), ["baseline_id"] = JValue.CreateNull() },
                    ["initial_position_sheet_m"] = new JArray(0.15, 0.24),
                    ["verification_tolerance"] = new JObject { ["value_abs_si"] = 1e-9,
                        ["position_abs_m"] = 1e-6, ["attachment_count_exact"] = true,
                        ["display_text_exact"] = false }
                }), ["assumptions"] = new JArray(), ["open_questions"] = new JArray()
            };
            File.WriteAllText(dimensionPlanPath, dimensionPlanValue.ToString());
            File.WriteAllBytes(drawingPath, new byte[] { 1, 2, 3, 4 });
            string dpHash = DrawingLayoutPlanContractValidator.FileSha256(dimensionPlanPath);
            string drawingHash = DrawingLayoutPlanContractValidator.FileSha256(drawingPath);
            File.WriteAllText(sidecarPath, new JObject
            {
                ["protocol_id"] = "solidworks-dimension-drawing-verification",
                ["verified"] = true, ["plan_id"] = "DP-G4", ["output_path"] = drawingPath,
                ["artifact_sha256"] = drawingHash, ["plan_file_path"] = dimensionPlanPath,
                ["plan_file_sha256"] = dpHash,
                ["plan_canonical_sha256"] = DrawingLayoutPlanContractValidator.Sha256(
                    System.Text.Encoding.UTF8.GetBytes(
                        DrawingLayoutPlanContractValidator.Canonicalize(dimensionPlanValue)
                            .ToString(Newtonsoft.Json.Formatting.None))),
                ["dimension_handles"] = new JObject { ["D-1"] = "D1@Drawing View1" },
                ["reopen_verification"] = new JObject { ["verified"] = true,
                    ["baseline_count"] = 0, ["planned_count"] = 1,
                    ["actual_total_count"] = 1, ["dimensions"] = new JArray(new JObject {
                        ["dimension_id"] = "D-1", ["selection_name"] = "D1@Drawing View1",
                        ["text"] = "10", ["hole_callout_variables"] = new JArray(),
                        ["tolerance"] = JValue.CreateNull(), ["value_si"] = 0.01,
                        ["model_persistent_references"] = new JArray("AQID", "BAUG") }) }
            }.ToString());
            string sidecarHash = DrawingLayoutPlanContractValidator.FileSha256(sidecarPath);
            string semanticHash = new string('3', 64), snapshotHash = new string('4', 64);
            string boundaryPath = Path.Combine(root, "g0.json");
            string qualificationPath = Path.Combine(root, "g0-qualification.json");
            File.WriteAllText(boundaryPath, "{}"); File.WriteAllText(qualificationPath, "{}");
            string boundaryHash = DrawingLayoutPlanContractValidator.FileSha256(boundaryPath);
            string qualificationHash = DrawingLayoutPlanContractValidator.FileSha256(qualificationPath);
            JArray objects = new JArray(
                Object("dim-object", "dimension:Front:D1@Drawing View1", "dimension_display_bounds", "Front"),
                Object("note-object", "note:Front:0", "note_text_bounds", "Front"),
                Object("leader-object", "leader:note:Front:0:0", "leader_bounds", "Front"),
                Object("view-object", "view:Front", "view_outline_bounds", "Front"),
                Object("frame-object", "sheet-border", "sheet_border_bounds", "Sheet"));
            File.WriteAllText(handoffPath, new JObject
            {
                ["protocol_id"] = "solidworks-drawing-layout-handoff", ["schema_version"] = "1.0",
                ["handoff_id"] = "layout-handoff-g4", ["status"] = "ready",
                ["upstream_artifacts"] = new JArray(
                    Ledger("dimension_plan", dimensionPlanPath, dpHash),
                    Ledger("dimensioned_drawing", drawingPath, drawingHash),
                    Ledger("dimension_verification_sidecar", sidecarPath, sidecarHash),
                    Ledger("boundary_capability_manifest", boundaryPath, boundaryHash),
                    Ledger("boundary_qualification", qualificationPath, qualificationHash)),
                ["dimension_semantics"] = new JObject { ["invariant_sha256"] = semanticHash,
                    ["dimensions"] = new JArray(new JObject { ["dimension_id"] = "D-1" }) },
                ["sheet"] = new JObject { ["safe_bounds_m"] = new JArray(0.01, 0.01, 0.41, 0.287) },
                ["objects"] = objects,
                ["constraints"] = new JObject {
                    ["frozen_objects"] = new JArray("frame-object"),
                    ["view_constraints"] = new JArray(new JObject { ["view"] = "Front" }),
                    ["locked_zones"] = new JArray() },
                ["boundary_capabilities"] = new JObject { ["required"] = new JArray(
                    "view_outline_bounds", "dimension_display_bounds", "note_text_bounds",
                    "leader_bounds", "sheet_border_bounds") },
                ["snapshots"] = new JObject { ["readonly_reopen_sha256"] = snapshotHash }
            }.ToString());
            string handoffHash = DrawingLayoutPlanContractValidator.FileSha256(handoffPath);
            string hash = new string('2', 64);
            return new JObject
            {
                ["$schema"] = "https://q3ds.local/contracts/solidworks-drawing-layout-plan-1.0.schema.json",
                ["protocol_id"] = "solidworks-drawing-layout-plan", ["schema_version"] = "1.0",
                ["plan_id"] = "layout-plan-g4", ["created_at_utc"] = "2026-08-15T10:00:00Z",
                ["producer"] = new JObject { ["name"] = "repository-layout-planner",
                    ["version"] = "0.1.0", ["ruleset_id"] = "layout-rules-v1",
                    ["ruleset_sha256"] = hash },
                ["execution_policy"] = Policy(), ["handoff"] = Artifact(handoffPath, handoffHash),
                ["handoff_id"] = "layout-handoff-g4",
                ["source_dimension_plan"] = Artifact(dimensionPlanPath, dpHash),
                ["source_drawing"] = Artifact(drawingPath, drawingHash),
                ["dimension_verification_sidecar"] = Artifact(sidecarPath, sidecarHash),
                ["configuration"] = "Default",
                ["source_invariants"] = new JObject { ["dimension_semantics_sha256"] = semanticHash,
                    ["dimension_ids"] = new JArray("D-1"), ["object_snapshot_sha256"] = snapshotHash,
                    ["object_ids"] = new JArray("dim-object", "note-object", "leader-object",
                        "view-object", "frame-object"), ["view_names"] = new JArray("Front"),
                    ["locked_object_ids"] = new JArray("frame-object"),
                    ["required_boundary_capabilities"] = new JArray("view_outline_bounds",
                        "dimension_display_bounds", "note_text_bounds", "leader_bounds",
                        "sheet_border_bounds") },
                ["authorization"] = new JObject { ["movable_view_names"] = new JArray("Front"),
                    ["scalable_view_names"] = new JArray("Front"),
                    ["allow_sheet_scale_change"] = true,
                    ["allowed_sheet_formats"] = new JArray(new JObject {
                        ["authorization_id"] = "sheet-approval-a3", ["format_id"] = "ISO-A3",
                        ["width_m"] = 0.42, ["height_m"] = 0.297, ["approved_by"] = "owner",
                        ["approved_at_utc"] = "2026-08-15T09:00:00Z",
                        ["approval_reference"] = "approved-fixture" }) },
                ["operations"] = Operations(), ["assumptions"] = new JArray("fixture"),
                ["open_questions"] = new JArray()
            };
        }

        private static JArray Operations() => new JArray(
            new JObject { ["operation_id"]="op-0", ["kind"]="set_dimension_hierarchy", ["sequence"]=0, ["dimension_id"]="D-1", ["tier"]="outer", ["stack_index"]=0 },
            new JObject { ["operation_id"]="op-1", ["kind"]="move_dimension", ["sequence"]=1, ["object_id"]="dim-object", ["dimension_id"]="D-1", ["target_position_sheet_m"]=new JArray(0.12,0.15), ["preserve_attachment"]=true },
            new JObject { ["operation_id"]="op-2", ["kind"]="move_annotation", ["sequence"]=2, ["object_id"]="note-object", ["target_position_sheet_m"]=new JArray(0.18,0.16) },
            new JObject { ["operation_id"]="op-3", ["kind"]="route_leader", ["sequence"]=3, ["object_id"]="leader-object", ["points_sheet_m"]=new JArray(new JArray(0.18,0.16),new JArray(0.2,0.17)), ["preserve_attachment"]=true },
            new JObject { ["operation_id"]="op-4", ["kind"]="move_view", ["sequence"]=4, ["view_name"]="Front", ["target_position_sheet_m"]=new JArray(0.15,0.11), ["preserve_alignment"]=true },
            new JObject { ["operation_id"]="op-5", ["kind"]="set_view_scale", ["sequence"]=5, ["view_name"]="Front", ["numerator"]=2, ["denominator"]=1 },
            new JObject { ["operation_id"]="op-6", ["kind"]="set_sheet_scale", ["sequence"]=6, ["numerator"]=1, ["denominator"]=2 },
            new JObject { ["operation_id"]="op-7", ["kind"]="set_sheet_format", ["sequence"]=7, ["authorization_id"]="sheet-approval-a3", ["format_id"]="ISO-A3", ["width_m"]=0.42, ["height_m"]=0.297 });

        private static JObject Policy() => new JObject {
            ["on_integrity_mismatch"]="fail", ["on_layout_violation"]="fail",
            ["on_unsupported_operation"]="fail", ["preserve_dimension_count"]=true,
            ["preserve_dimension_values"]=true, ["preserve_dimension_attachments"]=true,
            ["preserve_configuration"]=true, ["preserve_display_state"]=true,
            ["preserve_projection_method"]=true, ["preserve_section_definitions"]=true,
            ["preserve_model_associativity"]=true, ["preserve_frozen_geometry"]=true,
            ["allow_delete_objects"]=false, ["allow_new_manufacturing_annotations"]=false,
            ["allow_source_model_write"]=false, ["allow_upstream_drawing_overwrite"]=false,
            ["allow_partial_commit"]=false };
        private static JObject Artifact(string path, string hash) => new JObject
            { ["path"] = path, ["sha256"] = hash };
        private static JObject Ledger(string role, string path, string hash) => new JObject
            { ["role"] = role, ["path"] = path, ["sha256_before"] = hash,
                ["sha256_after"] = hash };
        private static JObject Geometry(string id, string reference, JArray values) => new JObject
            { ["entity_id"] = id, ["model_persistent_reference"] = reference,
                ["persistent_reference_kind"] = "entity", ["geometry_sheet_m"] = values };
        private static JObject Attachment(string id, string entity, string reference, string role) =>
            new JObject { ["attachment_id"] = id, ["entity_id"] = entity,
                ["model_persistent_reference"] = reference,
                ["persistent_reference_kind"] = "entity", ["role"] = role };
        private static JObject Object(string id, string source, string category, string view) =>
            new JObject { ["id"] = id, ["source_id"] = source, ["category"] = category,
                ["view"] = view, ["bounds"] = new JArray(0.1,0.1,0.11,0.11),
                ["source_api"] = "fixture", ["exact"] = true, ["collision_usable"] = true };

        private static string WriteCommittedG4(string root, DrawingLayoutExecutionPlan plan,
            string planPath, string planFileHash)
        {
            string output = Path.Combine(root, "final.SLDDRW");
            File.WriteAllBytes(output, new byte[] { 31, 32, 33 });
            string artifactHash = DrawingLayoutPlanContractValidator.FileSha256(output);
            string fingerprint = new string('d', 64);
            JObject dimensions = new JObject { ["count"] = 1,
                ["dimensions"] = new JArray(new JObject { ["view"] = "Front",
                    ["selection_name"] = "D1@Drawing View1", ["ordinal"] = 0,
                    ["value_si"] = 0.01, ["attached_entity_count"] = 2 }),
                ["fingerprint_sha256"] = new string('c', 64) };
            JArray views = new JArray(new JObject { ["name"] = "Front", ["view_type"] = 1,
                ["referenced_configuration"] = "Default", ["display_state"] = "",
                ["base_view"] = JValue.CreateNull(),
                ["section_definition_sha256"] = JValue.CreateNull() });
            JObject verification = new JObject { ["verified"] = true,
                ["dimension_semantics"] = dimensions, ["view_semantics"] = views,
                ["layout_fingerprint_sha256"] = fingerprint,
                ["snapshot"] = new JObject { ["views"] = new JArray(),
                    ["objects"] = new JArray() } };
            var frozen = new JObject
            {
                ["drawing_layout_plan"] = DrawingLayoutPlanContractValidator.FileSha256(planPath),
                ["handoff"] = DrawingLayoutPlanContractValidator.FileSha256(plan.Handoff.Path),
                ["dimension_plan"] = DrawingLayoutPlanContractValidator.FileSha256(
                    plan.SourceDimensionPlan.Path),
                ["source_drawing"] = DrawingLayoutPlanContractValidator.FileSha256(
                    plan.SourceDrawing.Path),
                ["dimension_verification_sidecar"] = DrawingLayoutPlanContractValidator
                    .FileSha256(plan.DimensionVerificationSidecar.Path)
            };
            foreach (var pair in plan.UpstreamDimensionArtifacts)
                frozen["dimension_plan." + pair.Key] =
                    DrawingLayoutPlanContractValidator.FileSha256(pair.Value.Path);
            JObject sidecar = new JObject
            {
                ["protocol_id"] = "solidworks-drawing-layout-verification",
                ["schema_version"] = "1.0", ["operation_id"] = "G4-contract",
                ["generated_at_utc"] = "2026-08-15T12:00:00Z", ["plan_id"] = plan.PlanId,
                ["plan_file_path"] = planPath, ["plan_file_sha256"] = planFileHash,
                ["plan_canonical_sha256"] = plan.PlanSha256,
                ["source_drawing_path"] = plan.SourceDrawing.Path,
                ["source_drawing_sha256"] = plan.SourceDrawing.Sha256,
                ["output_path"] = output, ["artifact_sha256"] = artifactHash,
                ["verified"] = true, ["bounded_cycles"] = new JArray(new JObject {
                    ["cycle"] = 1, ["rebuild"] = true, ["verified"] = true,
                    ["layout_fingerprint_sha256"] = fingerprint }),
                ["in_memory_verification"] = verification,
                ["reopen_verification"] = verification.DeepClone(), ["frozen_inputs"] = frozen
            };
            File.WriteAllText(output + ".layout-verification.json", sidecar.ToString());
            return output;
        }

        private static void BuildSupportedRegistries(string root, out string planPath,
            out string boundaryPath)
        {
            string hash = new string('a', 64);
            boundaryPath = Path.Combine(root, "supported-boundaries.json");
            string[] ids = { "view_outline_bounds", "dimension_display_bounds", "note_text_bounds",
                "leader_bounds", "view_label_bounds", "section_symbol_bounds", "center_element_bounds",
                "sheet_border_bounds", "title_block_bounds", "rebuild_drift", "save_reopen_drift" };
            File.WriteAllText(boundaryPath, new JObject {
                ["protocol_id"]="solidworks-drawing-layout-executor-capabilities",
                ["schema_version"]="1.0", ["registry_version"]="test-live",
                ["solidworks_revision"]="33.5.0", ["verification"]="live_complete",
                ["live_evidence"] = new JObject { ["qualification_sha256"] = hash },
                ["capabilities"] = new JArray(ids.Select(id => new JObject
                    { ["id"] = id, ["status"] = "supported" })) }.ToString());
            string boundaryHash = DrawingLayoutPlanContractValidator.FileSha256(boundaryPath);
            string[] operations = { "move_dimension", "move_annotation", "route_leader", "move_view",
                "set_dimension_hierarchy", "set_view_scale", "set_sheet_scale", "set_sheet_format" };
            string[] safety = { "dimension_semantic_preservation", "view_semantic_preservation",
                "object_identity_preservation", "collision_readback", "save_reopen_layout_fingerprint",
                "authorized_sheet_change" };
            planPath = Path.Combine(root, "supported-layout-plan-capabilities.json");
            File.WriteAllText(planPath, new JObject {
                ["protocol_id"]="solidworks-drawing-layout-plan-capabilities",
                ["schema_version"]="1.0", ["plan_protocol_id"]="solidworks-drawing-layout-plan",
                ["plan_schema_version"]="1.0", ["solidworks_target"]="2025 SP5",
                ["solidworks_revision"]="33.5.0", ["boundary_registry"] = new JObject {
                    ["protocol_id"]="solidworks-drawing-layout-executor-capabilities",
                    ["registry_version"]="test-live", ["manifest_sha256"] = boundaryHash },
                ["operations"] = JObject.FromObject(operations.ToDictionary(id => id,
                    id => (object)new { status="supported", verification="live", evidence_sha256=hash })),
                ["safety_elements"] = JObject.FromObject(safety.ToDictionary(id => id,
                    id => (object)new { status="supported", verification="live", evidence_sha256=hash }))
            }.ToString());
        }
        private static string NewRoot() { string path = Path.Combine(Path.GetTempPath(),
            "drawing-layout-g4-contract-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path); return path; }
        private static void Assert(bool condition, string message)
        { if (!condition) throw new InvalidOperationException(message); }
        private static void Pass(string name) { _passed++; Console.WriteLine("PASS " + name); }
        private static string Format(DrawingLayoutPlanContractError error) => error == null
            ? "unknown error" : error.Code + " " + error.JsonPointer + " " + error.Message;
    }
}
