using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace DimensionContractTests
{
    internal static class DimensionPlanF5ContractTests
    {
        private static readonly string[] Kinds =
        {
            "linear", "aligned", "diameter", "radius", "angular", "reference",
            "hole_diameter", "hole_depth", "hole_quantity", "hole_spacing",
            "hole_group_location", "overall", "step", "boss", "slot", "chamfer",
            "fillet", "symmetric"
        };

        public static int Run(string schemaPath, string registryPath)
        {
            var validator = new DimensionPlanContractValidator(Path.GetFullPath(schemaPath));
            foreach (string kind in Kinds)
            {
                JObject candidate = BuildPlan(kind, false);
                DimensionPlanDocument document; DimensionPlanContractError error;
                Assert(validator.TryParse(candidate, out document, out error), Format(error));
                DimensionPlanExecutionPlan compiled;
                Assert(new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                    out error), kind + ": " + Format(error));
                Assert(compiled.Dimensions[0].Kind == kind, "kind was not preserved: " + kind);
                Assert(new DimensionPlanCapabilityPreflight().TryValidateQualification(compiled,
                    Path.GetFullPath(registryPath), out error),
                    kind + " qualification: " + Format(error));
            }
            Console.WriteLine("ok - F5/F7 complete 18-kind compiler and qualification union");

            JObject tolerancePlan = BuildPlan("linear", true);
            tolerancePlan["dimensions"][0]["display_format"]["prefix"] = "TYP ";
            tolerancePlan["dimensions"][0]["display_format"]["suffix"] = " F7";
            DimensionPlanDocument toleranceDocument; DimensionPlanContractError toleranceError;
            Assert(validator.TryParse(tolerancePlan, out toleranceDocument, out toleranceError),
                Format(toleranceError));
            DimensionPlanExecutionPlan toleranceCompiled;
            Assert(new DimensionPlanExecutionCompiler().TryCompile(toleranceDocument,
                out toleranceCompiled, out toleranceError), Format(toleranceError));
            Assert(toleranceCompiled.Dimensions[0].Tolerance.Kind == "bilateral" &&
                toleranceCompiled.Dimensions[0].Tolerance.LowerSi == -0.0001 &&
                toleranceCompiled.Dimensions[0].Tolerance.UpperSi == 0.0002 &&
                toleranceCompiled.Dimensions[0].Prefix == "TYP " &&
                toleranceCompiled.Dimensions[0].Suffix == " F7",
                "trusted numeric tolerance was not preserved");
            Console.WriteLine("ok - F5 trusted numeric tolerance compiler");
            Assert(new DimensionPlanCapabilityPreflight().TryValidateQualification(
                toleranceCompiled, Path.GetFullPath(registryPath), out toleranceError),
                Format(toleranceError));
            Console.WriteLine("ok - F7 six execution elements qualification preflight");

            JObject fitPlan = BuildPlan("diameter", true);
            fitPlan["dimensions"][0]["tolerance"] = new JObject
            {
                ["kind"] = "fit", ["lower_si"] = JValue.CreateNull(),
                ["upper_si"] = JValue.CreateNull(), ["fit_code"] = "H7"
            };
            Assert(validator.TryParse(fitPlan, out toleranceDocument, out toleranceError),
                Format(toleranceError));
            Assert(new DimensionPlanExecutionCompiler().TryCompile(toleranceDocument,
                out toleranceCompiled, out toleranceError), Format(toleranceError));
            Assert(toleranceCompiled.Dimensions[0].Tolerance.FitCode == "H7",
                "fit code was not preserved");
            Console.WriteLine("ok - F5 trusted fit-code compiler");

            JObject ordinate = BuildPlan("hole_group_location", false);
            ordinate["dimensions"][0]["hierarchy"]["baseline_id"] = "datum-A";
            DimensionPlanDocument ordinateDocument; DimensionPlanContractError ordinateError;
            Assert(validator.TryParse(ordinate, out ordinateDocument, out ordinateError),
                Format(ordinateError));
            DimensionPlanExecutionPlan ordinateCompiled;
            Assert(new DimensionPlanExecutionCompiler().TryCompile(ordinateDocument,
                out ordinateCompiled, out ordinateError), Format(ordinateError));
            Assert(ordinateCompiled.Dimensions[0].UseOrdinate,
                "hole-group baseline did not compile to ordinate intent");
            Console.WriteLine("ok - F5 ordinate intent compiler");

            JObject baseline = BuildPlan("linear", false);
            JObject firstBaseline = (JObject)baseline["dimensions"][0];
            firstBaseline["hierarchy"]["baseline_id"] = "datum-B";
            firstBaseline["hierarchy"]["chain_id"] = "chain-B";
            JObject secondBaseline = (JObject)firstBaseline.DeepClone();
            secondBaseline["dimension_id"] = "D-2";
            secondBaseline["attachments"][1]["attachment_id"] = "A-4";
            secondBaseline["attachments"][1]["entity_id"] = "GE-4";
            secondBaseline["attachments"][1]["model_persistent_reference"] = "DQkN";
            secondBaseline["initial_position_sheet_m"] = new JArray(0.15, 0.25);
            ((JArray)baseline["dimensions"]).Add(secondBaseline);
            Assert(validator.TryParse(baseline, out ordinateDocument, out ordinateError),
                Format(ordinateError));
            Assert(new DimensionPlanExecutionCompiler().TryCompile(ordinateDocument,
                out ordinateCompiled, out ordinateError), Format(ordinateError));
            Assert(ordinateCompiled.Dimensions.Count == 2 &&
                ordinateCompiled.Dimensions.All(item => item.BaselineId == "datum-B" &&
                    item.ChainId == "chain-B"),
                "baseline/chain hierarchy was not preserved");
            Console.WriteLine("ok - F5 shared-datum baseline and chain compiler");

            JObject singletonBaseline = BuildPlan("linear", false);
            singletonBaseline["dimensions"][0]["hierarchy"]["baseline_id"] = "datum-single";
            Assert(validator.TryParse(singletonBaseline, out ordinateDocument, out ordinateError),
                Format(ordinateError));
            Assert(!new DimensionPlanExecutionCompiler().TryCompile(ordinateDocument,
                out ordinateCompiled, out ordinateError) &&
                ordinateError.Code == "DIMENSION_PLAN_COMPILE_INVALID",
                "single-member non-ordinate baseline group was accepted");
            Console.WriteLine("ok - F5 incomplete baseline rejected before COM");

            Assert(!new DimensionPlanCapabilityPreflight().TryValidate(toleranceCompiled,
                Path.GetFullPath(registryPath), out toleranceError) &&
                toleranceError.Code == "DIMENSION_CAPABILITY_BLOCKED",
                "F5 capability was promoted without live evidence");
            Console.WriteLine("ok - F5 advanced execution remains live-evidence gated");

            RunTrustedPreflight(validator);
            return 10;
        }

        internal static JObject BuildPlan(string kind, bool approvedTolerance)
        {
            string root = Path.Combine(Path.GetTempPath(), "dimension-f5-contract");
            string hash = new string('b', 64);
            JObject dimension = Dimension(kind, approvedTolerance);
            return new JObject
            {
                ["$schema"] = "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json",
                ["protocol_id"] = "solidworks-dimension-plan", ["schema_version"] = "1.0",
                ["plan_id"] = "DP-F5", ["created_at_utc"] = "2026-08-13T00:00:00Z",
                ["producer"] = new JObject { ["name"] = "contract-test", ["version"] = "1.0.0",
                    ["ruleset_id"] = "f5", ["ruleset_sha256"] = hash },
                ["execution_policy"] = new JObject
                {
                    ["on_integrity_mismatch"] = "fail", ["on_selection_ambiguity"] = "fail",
                    ["on_unsupported_dimension"] = "fail", ["on_layout_violation"] = "fail",
                    ["allow_source_model_write"] = false,
                    ["allow_upstream_drawing_overwrite"] = false,
                    ["allow_partial_commit"] = false
                },
                ["handoff"] = Artifact(Path.Combine(root, "dimension-planning-handoff.json"), hash),
                ["handoff_id"] = "DMH-F5",
                ["source_model"] = Artifact(Path.Combine(root, "part.SLDPRT"), hash),
                ["source_drawing"] = Artifact(Path.Combine(root, "views.SLDDRW"), hash),
                ["view_plan"] = Artifact(Path.Combine(root, "view_plan.json"), hash),
                ["verification_sidecar"] = Artifact(Path.Combine(root, "views.verify.json"), hash),
                ["configuration"] = "Default", ["dimensions"] = new JArray(dimension),
                ["assumptions"] = new JArray(), ["open_questions"] = new JArray()
            };
        }

        private static JObject Dimension(string kind, bool approvedTolerance)
        {
            var single = new HashSet<string>(new[] { "diameter", "radius", "reference",
                "hole_diameter", "hole_depth", "hole_quantity", "boss", "fillet" },
                StringComparer.Ordinal);
            JArray attachments = new JArray(
                Attachment("A-1", "GE-1", "AQID", "first"),
                Attachment("A-2", "GE-2", "BAUG", "second"));
            if (single.Contains(kind)) attachments.RemoveAt(1);
            if (kind == "symmetric")
                attachments.Add(Attachment("A-3", "GE-3", "BwgJ", "symmetry_axis"));
            string quantity = kind == "angular" ? "angle" :
                (kind == "hole_quantity" ? "count" : "length");
            double nominal = kind == "hole_quantity" ? 4 : 0.01;
            JObject source = approvedTolerance
                ? new JObject { ["source_tier"] = "user_confirmed_input",
                    ["approved_input_ids"] = new JArray("NOMINAL", "LOWER", "UPPER", "FIT") }
                : new JObject { ["source_tier"] = "model_or_pmi",
                    ["handoff_collection"] = "model_driven_dimensions",
                    ["source_ids"] = new JArray("MD-1") };
            return new JObject
            {
                ["dimension_id"] = "D-1", ["kind"] = kind, ["source"] = source,
                ["target_view_id"] = "front", ["attachments"] = attachments,
                ["feature_ids"] = new JArray("MF-1"),
                ["value"] = new JObject { ["value_mode"] = approvedTolerance
                    ? "approved_value" : "model_driven", ["quantity_kind"] = quantity,
                    ["nominal_si"] = nominal },
                ["tolerance"] = approvedTolerance ? new JObject
                    { ["kind"] = "bilateral", ["lower_si"] = -0.0001,
                        ["upper_si"] = 0.0002, ["fit_code"] = JValue.CreateNull() }
                    : JValue.CreateNull(),
                ["display_format"] = new JObject { ["unit"] = quantity == "count" ? "count" :
                    (quantity == "angle" ? "degree" : "mm"), ["precision"] = 2,
                    ["prefix"] = "", ["suffix"] = "", ["show_parentheses"] = kind == "reference",
                    ["show_units"] = false, ["dual_units"] = false },
                ["dimension_zone_id"] = "DZ-front-top",
                ["hierarchy"] = new JObject { ["level"] = kind == "reference" ? "reference" :
                    "manufacturing", ["priority"] = 1, ["chain_id"] = JValue.CreateNull(),
                    ["baseline_id"] = JValue.CreateNull() },
                ["initial_position_sheet_m"] = new JArray(0.15, 0.24),
                ["verification_tolerance"] = new JObject { ["value_abs_si"] = 1e-9,
                    ["position_abs_m"] = 1e-6, ["attachment_count_exact"] = true,
                    ["display_text_exact"] = false }
            };
        }

        private static JObject Attachment(string id, string entity, string reference, string role) =>
            new JObject { ["attachment_id"] = id, ["entity_id"] = entity,
                ["model_persistent_reference"] = reference,
                ["persistent_reference_kind"] = "entity", ["role"] = role };
        private static JObject Artifact(string path, string hash) =>
            new JObject { ["path"] = path, ["sha256"] = hash };
        private static void RunTrustedPreflight(DimensionPlanContractValidator validator)
        {
            string root = Path.Combine(Path.GetTempPath(), "dimension-f5-preflight-" +
                Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string model = Path.Combine(root, "part.SLDPRT");
            string drawing = Path.Combine(root, "views.SLDDRW");
            string viewPlan = Path.Combine(root, "view_plan.json");
            string sidecar = Path.Combine(root, "views.verify.json");
            File.WriteAllBytes(model, Encoding.UTF8.GetBytes("model"));
            File.WriteAllBytes(drawing, Encoding.UTF8.GetBytes("drawing"));
            File.WriteAllText(viewPlan, "{}", new UTF8Encoding(false));
            File.WriteAllText(sidecar, "{}", new UTF8Encoding(false));
            string modelHash = FileHash(model), drawingHash = FileHash(drawing);
            string viewHash = FileHash(viewPlan), sidecarHash = FileHash(sidecar);
            string handoffPath = Path.Combine(root, "dimension-planning-handoff.json");
            JObject handoff = new JObject
            {
                ["protocol_id"] = "solidworks-dimension-planning-handoff",
                ["schema_version"] = "1.0", ["handoff_id"] = "DMH-F5-PREFLIGHT",
                ["status"] = "ready",
                ["source_model"] = new JObject { ["path"] = model, ["sha256"] = modelHash,
                    ["configuration"] = "Default", ["save_flag"] = false },
                ["drawing_context"] = new JObject { ["path"] = drawing },
                ["upstream_artifacts"] = new JArray(
                    Ledger("source_model", model, modelHash),
                    Ledger("verified_drawing", drawing, drawingHash),
                    Ledger("view_plan", viewPlan, viewHash),
                    Ledger("verification_sidecar", sidecar, sidecarHash)),
                ["views"] = new JArray(new JObject
                {
                    ["view_id"] = "front", ["solidworks_name"] = "Q3DS_VP_front",
                    ["projected_geometry"] = new JArray(
                        Geometry("GE-1", "AQID", new JArray(0.1, 0.1, 0.2, 0.1)),
                        Geometry("GE-2", "BAUG", new JArray(0.1, 0.2, 0.2, 0.2)))
                }),
                ["model_driven_dimensions"] = new JArray(),
                ["manufacturing_features"] = new JArray(new JObject
                {
                    ["feature_id"] = "MF-1", ["classification"] = "hole"
                }),
                ["approved_user_inputs"] = new JArray(
                    ApprovedQuantity("NOMINAL", 0.01), ApprovedQuantity("LOWER", -0.0001),
                    ApprovedQuantity("UPPER", 0.0002), ApprovedText("FIT", "H7")),
                ["reference_measurements"] = new JArray()
            };
            WriteJson(handoffPath, handoff);

            JObject candidate = BuildPlan("linear", true);
            candidate["handoff_id"] = "DMH-F5-PREFLIGHT";
            candidate["handoff"] = Artifact(handoffPath, FileHash(handoffPath));
            candidate["source_model"] = Artifact(model, modelHash);
            candidate["source_drawing"] = Artifact(drawing, drawingHash);
            candidate["view_plan"] = Artifact(viewPlan, viewHash);
            candidate["verification_sidecar"] = Artifact(sidecar, sidecarHash);
            string planPath = Path.Combine(root, "dimension_plan.json");
            WriteJson(planPath, candidate);
            string planHash = FileHash(planPath);
            DimensionPlanDocument document; DimensionPlanContractError error;
            Assert(validator.TryParse(candidate, out document, out error), Format(error));
            DimensionPlanExecutionPlan compiled;
            Assert(new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                out error), Format(error));
            DimensionPlanTransactionPaths paths;
            Assert(new DimensionPlanTransactionPreflight().TryValidate(compiled, planPath,
                planHash, Path.Combine(root, "dimensioned.SLDDRW"), out paths, out error),
                Format(error));
            Assert(compiled.Dimensions[0].FitTarget == "hole",
                "hole fit target was not resolved from the frozen feature");
            Console.WriteLine("ok - F5 trusted tolerance handoff preflight");

            compiled.Dimensions[0].Tolerance.LowerSi = -0.5;
            Assert(!new DimensionPlanTransactionPreflight().TryValidate(compiled, planPath,
                planHash, Path.Combine(root, "rejected.SLDDRW"), out paths, out error) &&
                error.Code == "DIMENSION_TOLERANCE_UNTRUSTED",
                "unapproved tolerance value passed native preflight");
            Console.WriteLine("ok - F5 unapproved tolerance rejected before COM");
        }

        private static JObject Ledger(string role, string path, string hash) => new JObject
        { ["role"] = role, ["path"] = path, ["sha256_before"] = hash, ["sha256_after"] = hash };
        private static JObject Geometry(string id, string reference, JArray values) => new JObject
        { ["entity_id"] = id, ["model_persistent_reference"] = reference,
            ["persistent_reference_kind"] = "entity", ["geometry_sheet_m"] = values };
        private static JObject ApprovedQuantity(string id, double value) => new JObject
        { ["input_id"] = id, ["target_feature_ids"] = new JArray("MF-1"),
            ["value"] = new JObject { ["kind"] = "quantity", ["quantity_kind"] = "length",
                ["value_si"] = value } };
        private static JObject ApprovedText(string id, string value) => new JObject
        { ["input_id"] = id, ["target_feature_ids"] = new JArray("MF-1"),
            ["value"] = new JObject { ["kind"] = "exact_text", ["text"] = value } };
        private static void WriteJson(string path, JObject value) => File.WriteAllText(path,
            value.ToString(Formatting.Indented) + Environment.NewLine, new UTF8Encoding(false));
        private static string FileHash(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "")
                    .ToLowerInvariant();
        }
        private static string Format(DimensionPlanContractError error) => error == null ? "<none>" :
            error.Code + " " + error.JsonPointer + " " + error.Message;
        private static void Assert(bool value, string message)
        { if (!value) throw new InvalidOperationException(message); }
    }
}
