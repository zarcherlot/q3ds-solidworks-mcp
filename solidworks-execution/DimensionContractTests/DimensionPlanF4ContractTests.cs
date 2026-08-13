using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace DimensionContractTests
{
    internal static class DimensionPlanF4ContractTests
    {
        public static int Run(string schemaPath, string registryPath)
        {
            if (string.IsNullOrWhiteSpace(schemaPath) || !File.Exists(schemaPath))
                throw new InvalidOperationException("DIMENSION_PLAN_SCHEMA_PATH is invalid.");
            var validator = new DimensionPlanContractValidator(Path.GetFullPath(schemaPath));
            DimensionPlanDocument document; DimensionPlanContractError error;
            JObject candidate = BuildPlan("linear");
            Assert(validator.TryParse(candidate, out document, out error), Format(error));
            Console.WriteLine("ok - F4 DimensionPlan hash-locked schema");

            DimensionPlanExecutionPlan compiled;
            Assert(new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                out error), Format(error));
            Assert(compiled.Dimensions.Count == 1 && compiled.Dimensions[0].Kind == "linear",
                "compiled F4 dimension missing");
            Console.WriteLine("ok - F4 native MVP compiler");

            JObject aligned = BuildPlan("aligned");
            Assert(validator.TryParse(aligned, out document, out error), Format(error));
            Assert(new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                out error), Format(error));
            Console.WriteLine("ok - F5 aligned dimension compiler");

            JObject tolerance = BuildPlan("linear");
            tolerance["dimensions"][0]["tolerance"] = new JObject
            {
                ["kind"] = "bilateral", ["lower_si"] = -0.0001,
                ["upper_si"] = 0.0001, ["fit_code"] = JValue.CreateNull()
            };
            Assert(validator.TryParse(tolerance, out document, out error), Format(error));
            Assert(!new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                out error) && error.Code == "DIMENSION_PLAN_COMPILE_INVALID",
                "untrusted model tolerance was accepted");
            Console.WriteLine("ok - F5 compiler rejects untrusted model tolerance");

            JObject quantity = BuildPlan("hole_quantity");
            ((JArray)quantity["dimensions"][0]["attachments"]).RemoveAt(1);
            quantity["dimensions"][0]["value"]["quantity_kind"] = "count";
            quantity["dimensions"][0]["value"]["nominal_si"] = 2.5;
            quantity["dimensions"][0]["display_format"]["unit"] = "count";
            Assert(validator.TryParse(quantity, out document, out error), Format(error));
            Assert(!new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                out error) && error.Code == "DIMENSION_PLAN_COMPILE_INVALID",
                "fractional hole quantity was accepted");
            Console.WriteLine("ok - F4 hole quantity is an exact integer count");

            Assert(validator.TryParse(candidate, out document, out error), Format(error));
            Assert(new DimensionPlanExecutionCompiler().TryCompile(document, out compiled,
                out error), Format(error));
            Assert(!new DimensionPlanCapabilityPreflight().TryValidate(compiled,
                Path.GetFullPath(registryPath), out error) &&
                error.Code == "DIMENSION_CAPABILITY_BLOCKED",
                "planned capabilities were promoted without live evidence");
            Console.WriteLine("ok - F4 production execution requires live capability evidence");
            Assert(new DimensionPlanCapabilityPreflight().TryValidateQualification(compiled,
                Path.GetFullPath(registryPath), out error), Format(error));
            Console.WriteLine("ok - F7 qualification accepts planned capabilities without promotion");

            string matrixPath = Path.Combine(Path.GetTempPath(), "dimension-f7-binding-" +
                Guid.NewGuid().ToString("N") + ".json");
            try
            {
                string planPath = Path.Combine(Path.GetTempPath(), "dimension_plan.json");
                string outputPath = Path.Combine(Path.GetTempPath(), "qualified.SLDDRW");
                string planFileSha = new string('c', 64);
                string requestSha = new string('d', 64);
                string matrixCanonicalSha = new string('e', 64);
                var matrix = new JObject
                {
                    ["protocol_id"] = "solidworks-dimension-f7-matrix-request",
                    ["schema_version"] = "1.0",
                    ["solidworks_revision"] = "33.5.0",
                    ["cases"] = new JArray(new JObject
                    {
                        ["case_id"] = "F7-CONTRACT",
                        ["plan_path"] = planPath,
                        ["plan_file_sha256"] = planFileSha,
                        ["plan_canonical_sha256"] = matrixCanonicalSha,
                        ["planning_request_sha256"] = requestSha,
                        ["output_path"] = outputPath
                    })
                };
                File.WriteAllText(matrixPath, matrix.ToString(), new UTF8Encoding(false));
                string matrixSha = DimensionPlanContractValidator.FileSha256(matrixPath);
                string acceptedCanonicalSha;
                Assert(new DimensionPlanQualificationPreflight().TryValidate(compiled,
                    planPath, planFileSha, outputPath, matrixPath, matrixSha, requestSha,
                    "F7-CONTRACT", out acceptedCanonicalSha, out error), Format(error));
                Assert(acceptedCanonicalSha == matrixCanonicalSha,
                    "F7 matrix canonical hash was not propagated to the qualification transaction");
                Assert(acceptedCanonicalSha != compiled.PlanSha256,
                    "cross-language canonical hash fixture did not exercise the mismatch");
            }
            finally
            {
                if (File.Exists(matrixPath)) File.Delete(matrixPath);
            }
            Console.WriteLine("ok - F7 matrix canonical hash survives cross-language formatting");
            return 8;
        }

        private static JObject BuildPlan(string kind)
        {
            string root = Path.Combine(Path.GetTempPath(), "dimension-f4-contract");
            string hash = new string('a', 64);
            return new JObject
            {
                ["$schema"] = "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json",
                ["protocol_id"] = "solidworks-dimension-plan", ["schema_version"] = "1.0",
                ["plan_id"] = "DP-F4", ["created_at_utc"] = "2026-08-13T00:00:00Z",
                ["producer"] = new JObject { ["name"] = "contract-test", ["version"] = "1.0.0",
                    ["ruleset_id"] = "f4", ["ruleset_sha256"] = hash },
                ["execution_policy"] = new JObject
                {
                    ["on_integrity_mismatch"] = "fail", ["on_selection_ambiguity"] = "fail",
                    ["on_unsupported_dimension"] = "fail", ["on_layout_violation"] = "fail",
                    ["allow_source_model_write"] = false,
                    ["allow_upstream_drawing_overwrite"] = false,
                    ["allow_partial_commit"] = false
                },
                ["handoff"] = Artifact(Path.Combine(root, "dimension-planning-handoff.json"), hash),
                ["handoff_id"] = "DMH-F4", ["source_model"] = Artifact(Path.Combine(root, "part.SLDPRT"), hash),
                ["source_drawing"] = Artifact(Path.Combine(root, "views.SLDDRW"), hash),
                ["view_plan"] = Artifact(Path.Combine(root, "view_plan.json"), hash),
                ["verification_sidecar"] = Artifact(Path.Combine(root, "views.verify.json"), hash),
                ["configuration"] = "Default", ["dimensions"] = new JArray(Dimension(kind)),
                ["assumptions"] = new JArray(), ["open_questions"] = new JArray()
            };
        }

        private static JObject Dimension(string kind)
        {
            return new JObject
            {
                ["dimension_id"] = "D-1", ["kind"] = kind,
                ["source"] = new JObject { ["source_tier"] = "model_or_pmi",
                    ["handoff_collection"] = "model_driven_dimensions",
                    ["source_ids"] = new JArray("MD-1") },
                ["target_view_id"] = "front",
                ["attachments"] = new JArray(
                    Attachment("A-1", "GE-1", "AQID", "first"),
                    Attachment("A-2", "GE-2", "BAUG", "second")),
                ["feature_ids"] = new JArray("MF-1"),
                ["value"] = new JObject { ["value_mode"] = "model_driven",
                    ["quantity_kind"] = "length", ["nominal_si"] = 0.01 },
                ["tolerance"] = JValue.CreateNull(),
                ["display_format"] = new JObject { ["unit"] = "mm", ["precision"] = 2,
                    ["prefix"] = "", ["suffix"] = "", ["show_parentheses"] = false,
                    ["show_units"] = false, ["dual_units"] = false },
                ["dimension_zone_id"] = "DZ-front-top",
                ["hierarchy"] = new JObject { ["level"] = "manufacturing", ["priority"] = 1,
                    ["chain_id"] = JValue.CreateNull(), ["baseline_id"] = JValue.CreateNull() },
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
        private static string Format(DimensionPlanContractError error) => error == null ? "<none>" :
            error.Code + " " + error.JsonPointer + " " + error.Message;
        private static void Assert(bool value, string message)
        { if (!value) throw new InvalidOperationException(message); }
    }
}
