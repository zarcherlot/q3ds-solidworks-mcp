using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>G5 COM-free committed-layout, sidecar and recursive-input gate.</summary>
    internal sealed class DrawingLayoutPlanVerificationPreflight
    {
        internal const string VerificationSchemaSha256 =
            "6c1c4dd0c7aa273183082416b2e1fa34c3432b2e0c5698fc9aec8b7e6dd7f00d";

        public bool TryValidate(DrawingLayoutExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, string dimensionPlanSchemaPath,
            string verificationSchemaPath, out DrawingLayoutVerificationInputs inputs,
            out DrawingLayoutPlanContractError error)
        {
            inputs = null; error = null;
            string directory;
            try { directory = Path.GetDirectoryName(Path.GetFullPath(requestedOutputPath)); }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_OUTPUT_PATH_INVALID",
                "/output_path", ex.Message, out error); }
            string probe = Path.Combine(directory, ".q3ds-layout-verify-" +
                Guid.NewGuid().ToString("N") + ".SLDDRW");
            DrawingLayoutTransactionPaths transaction;
            if (!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                planSha256, probe, out transaction, out error)) return false;

            string outputPath = Path.GetFullPath(requestedOutputPath);
            if (!outputPath.EndsWith(".SLDDRW", StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(outputPath))
                return Fail("DRAWING_LAYOUT_OUTPUT_MISSING", "/output_path",
                    "Independent verification requires an existing final .SLDDRW.", out error);
            string reportPath = outputPath + ".layout-verification.json";
            if (!File.Exists(reportPath))
                return Fail("DRAWING_LAYOUT_VERIFICATION_REPORT_MISSING", "/output_path",
                    "The G4 layout verification sidecar is missing.", out error);
            IEnumerable<string> frozenPaths = new[] { transaction.PlanPath, plan.Handoff.Path,
                plan.SourceDimensionPlan.Path, plan.SourceDrawing.Path,
                plan.DimensionVerificationSidecar.Path }.Concat(
                    plan.UpstreamDimensionArtifacts.Values.Select(item => item.Path));
            if (frozenPaths.Any(path => PathEquals(path, outputPath) ||
                    PathEquals(path, reportPath)))
                return Fail("DRAWING_LAYOUT_OUTPUT_ALIASES_INPUT", "/output_path",
                    "The final drawing or layout sidecar aliases a frozen input.", out error);

            JObject report;
            try
            {
                report = DrawingLayoutPlanTransactionPreflight.Load(reportPath);
                var schema = new ViewPlanContractValidator(verificationSchemaPath,
                    VerificationSchemaSha256, "DrawingLayoutVerification",
                    "DRAWING_LAYOUT_VERIFICATION_REPORT_INVALID");
                ViewPlanContractError schemaError;
                if (!schema.TryValidate(report, out schemaError))
                    return Fail(schemaError.Code, schemaError.JsonPointer,
                        schemaError.Message, out error);
            }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_VERIFICATION_REPORT_INVALID",
                "/output_path", ex.Message, out error); }
            if (report.Value<string>("plan_id") != plan.PlanId ||
                !PathEquals(report.Value<string>("plan_file_path"), transaction.PlanPath) ||
                !HashEquals(report.Value<string>("plan_file_sha256"),
                    transaction.PlanFileSha256) ||
                !HashEquals(report.Value<string>("plan_canonical_sha256"), plan.PlanSha256) ||
                !PathEquals(report.Value<string>("source_drawing_path"), plan.SourceDrawing.Path) ||
                !HashEquals(report.Value<string>("source_drawing_sha256"),
                    plan.SourceDrawing.Sha256) ||
                !PathEquals(report.Value<string>("output_path"), outputPath))
                return Fail("DRAWING_LAYOUT_VERIFICATION_REPORT_MISMATCH", "/output_path",
                    "G4 sidecar does not bind the exact plan, source and final drawing.", out error);
            string artifactHash = DrawingLayoutPlanContractValidator.FileSha256(outputPath);
            if (!HashEquals(report.Value<string>("artifact_sha256"), artifactHash))
                return Fail("DRAWING_LAYOUT_OUTPUT_HASH_MISMATCH", "/output_path",
                    "Final drawing hash differs from the G4 sidecar.", out error);
            if (!ValidateFrozenInputs(report["frozen_inputs"] as JObject, plan,
                transaction.PlanPath, out error)) return false;

            JObject memory = report["in_memory_verification"] as JObject;
            JObject reopen = report["reopen_verification"] as JObject;
            JArray cycles = report["bounded_cycles"] as JArray;
            if (memory == null || reopen == null || cycles == null || cycles.Count < 1 ||
                cycles.Count > 3 || memory.Value<bool?>("verified") != true ||
                reopen.Value<bool?>("verified") != true ||
                memory["view_semantics"] is not JArray || reopen["view_semantics"] is not JArray ||
                !HashEquals(memory.Value<string>("layout_fingerprint_sha256"),
                    reopen.Value<string>("layout_fingerprint_sha256")) ||
                !JToken.DeepEquals(memory["dimension_semantics"],
                    reopen["dimension_semantics"]) ||
                !JToken.DeepEquals(memory["view_semantics"], reopen["view_semantics"]))
                return Fail("DRAWING_LAYOUT_VERIFICATION_REPORT_INVALID", "/output_path",
                    "G4 sidecar lacks matching in-memory and read-only-reopen evidence.", out error);
            for (int index = 0; index < cycles.Count; index++)
            {
                JObject cycle = cycles[index] as JObject;
                if (cycle == null || cycle.Value<int?>("cycle") != index + 1 ||
                    (index + 1 == cycles.Count && cycle.Value<bool?>("verified") != true))
                    return Fail("DRAWING_LAYOUT_VERIFICATION_REPORT_INVALID", "/output_path",
                        "Bounded-cycle evidence is incomplete or non-contiguous.", out error);
            }

            DimensionPlanExecutionPlan dimensionPlan;
            try
            {
                var validator = new DimensionPlanContractValidator(dimensionPlanSchemaPath);
                DimensionPlanDocument document; DimensionPlanContractError dimensionError;
                JObject candidate = DrawingLayoutPlanTransactionPreflight.Load(
                    plan.SourceDimensionPlan.Path);
                if (!validator.TryParse(candidate, out document, out dimensionError) ||
                    !new DimensionPlanExecutionCompiler().TryCompile(document,
                        out dimensionPlan, out dimensionError))
                    return Fail(dimensionError.Code, dimensionError.JsonPointer,
                        dimensionError.Message, out error);
                string dimensionProbe = Path.Combine(directory, ".q3ds-layout-dimension-verify-" +
                    Guid.NewGuid().ToString("N") + ".SLDDRW");
                DimensionPlanTransactionPaths ignored;
                if (!new DimensionPlanTransactionPreflight().TryValidate(dimensionPlan,
                    plan.SourceDimensionPlan.Path, plan.SourceDimensionPlan.Sha256,
                    dimensionProbe, out ignored, out dimensionError))
                    return Fail(dimensionError.Code, dimensionError.JsonPointer,
                        dimensionError.Message, out error);
            }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_DIMENSION_PLAN_INVALID",
                "/source_dimension_plan", ex.Message, out error); }
            if (!ApplyAuthorizedDimensionPositions(plan, dimensionPlan, out error)) return false;

            int baselineCount; Dictionary<string, string> handles, fingerprints;
            if (!ReadDimensionEvidence(plan.DimensionVerificationSidecar.Path, dimensionPlan,
                out baselineCount, out handles, out fingerprints, out error)) return false;
            inputs = new DrawingLayoutVerificationInputs
            {
                OutputPath = outputPath, ReportPath = reportPath,
                ArtifactSha256 = artifactHash, ExpectedLayoutFingerprint =
                    reopen.Value<string>("layout_fingerprint_sha256"),
                ExpectedDimensionSemantics = (JObject)memory["dimension_semantics"].DeepClone(),
                ExpectedViewSemantics = (JArray)memory["view_semantics"].DeepClone(),
                DimensionPlan = dimensionPlan, DimensionBaselineCount = baselineCount,
                DimensionHandles = handles, DimensionFingerprints = fingerprints
            };
            return true;
        }

        private static bool ApplyAuthorizedDimensionPositions(DrawingLayoutExecutionPlan layout,
            DimensionPlanExecutionPlan dimensionPlan, out DrawingLayoutPlanContractError error)
        {
            error = null;
            foreach (DrawingLayoutExecutionOperation move in layout.Operations.Where(item =>
                item.Kind == "move_dimension"))
            {
                DimensionPlanExecutionDimension[] matches = dimensionPlan.Dimensions.Where(item =>
                    item.DimensionId == move.DimensionId).ToArray();
                if (matches.Length != 1)
                    return Fail("DRAWING_LAYOUT_DIMENSION_BINDING_MISMATCH", "/operations",
                        "A moved dimension does not uniquely bind DimensionPlan: " +
                        move.DimensionId, out error);
                matches[0].PositionX = move.Target[0]; matches[0].PositionY = move.Target[1];
            }
            return true;
        }

        private static bool ReadDimensionEvidence(string path,
            DimensionPlanExecutionPlan dimensionPlan, out int baselineCount,
            out Dictionary<string, string> handles,
            out Dictionary<string, string> fingerprints,
            out DrawingLayoutPlanContractError error)
        {
            baselineCount = -1; handles = new Dictionary<string, string>(StringComparer.Ordinal);
            fingerprints = new Dictionary<string, string>(StringComparer.Ordinal); error = null;
            JObject sidecar;
            try { sidecar = DrawingLayoutPlanTransactionPreflight.Load(path); }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_DIMENSION_SIDECAR_INVALID",
                "/dimension_verification_sidecar", ex.Message, out error); }
            JObject handleRows = sidecar["dimension_handles"] as JObject;
            JObject reopen = sidecar["reopen_verification"] as JObject;
            JArray dimensions = reopen != null ? reopen["dimensions"] as JArray : null;
            var ids = new HashSet<string>(dimensionPlan.Dimensions.Select(item =>
                item.DimensionId), StringComparer.Ordinal);
            if (handleRows == null || dimensions == null ||
                !ids.SetEquals(handleRows.Properties().Select(item => item.Name)) ||
                dimensions.Count != ids.Count || reopen.Value<bool?>("verified") != true ||
                reopen.Value<int?>("baseline_count") == null)
                return Fail("DRAWING_LAYOUT_DIMENSION_SIDECAR_INVALID",
                    "/dimension_verification_sidecar",
                    "Source dimension sidecar lacks complete persisted evidence.", out error);
            baselineCount = reopen.Value<int>("baseline_count");
            foreach (string id in ids)
            {
                JObject[] rows = dimensions.OfType<JObject>().Where(row =>
                    row.Value<string>("dimension_id") == id).ToArray();
                string handle = handleRows.Value<string>(id);
                if (rows.Length != 1 || String.IsNullOrWhiteSpace(handle) ||
                    rows[0].Value<string>("selection_name") != handle ||
                    rows[0]["hole_callout_variables"] is not JArray ||
                    rows[0].Property("tolerance") == null || rows[0]["text"] == null)
                    return Fail("DRAWING_LAYOUT_DIMENSION_SIDECAR_INVALID",
                        "/dimension_verification_sidecar",
                        "Dimension evidence is incomplete: " + id, out error);
                handles[id] = handle;
                fingerprints[id] = new JObject
                {
                    ["text"] = rows[0]["text"].DeepClone(),
                    ["hole_callout_variables"] = rows[0]["hole_callout_variables"].DeepClone(),
                    ["tolerance"] = rows[0]["tolerance"].DeepClone()
                }.ToString(Formatting.None);
            }
            return true;
        }

        private static bool ValidateFrozenInputs(JObject frozen,
            DrawingLayoutExecutionPlan plan, string planPath,
            out DrawingLayoutPlanContractError error)
        {
            error = null;
            var expected = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["drawing_layout_plan"] = planPath, ["handoff"] = plan.Handoff.Path,
                ["dimension_plan"] = plan.SourceDimensionPlan.Path,
                ["source_drawing"] = plan.SourceDrawing.Path,
                ["dimension_verification_sidecar"] = plan.DimensionVerificationSidecar.Path
            };
            foreach (KeyValuePair<string, DrawingLayoutArtifactBinding> pair in
                plan.UpstreamDimensionArtifacts)
                expected["dimension_plan." + pair.Key] = pair.Value.Path;
            if (frozen == null || !new HashSet<string>(frozen.Properties().Select(item =>
                    item.Name), StringComparer.Ordinal).SetEquals(expected.Keys))
                return Fail("DRAWING_LAYOUT_FROZEN_INPUT_MISMATCH", "/output_path",
                    "G4 sidecar frozen-input inventory is incomplete.", out error);
            foreach (KeyValuePair<string, string> pair in expected)
                if (!HashEquals(frozen.Value<string>(pair.Key),
                    DrawingLayoutPlanContractValidator.FileSha256(pair.Value)))
                    return Fail("DRAWING_LAYOUT_FROZEN_INPUT_MISMATCH", "/output_path",
                        "Frozen input differs from G4 sidecar: " + pair.Key, out error);
            return true;
        }
        private static bool PathEquals(string first, string second)
        { try { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
            StringComparison.OrdinalIgnoreCase); } catch { return false; } }
        private static bool HashEquals(string first, string second) =>
            !String.IsNullOrEmpty(first) && String.Equals(first, second,
                StringComparison.OrdinalIgnoreCase);
        private static bool Fail(string code, string pointer, string message,
            out DrawingLayoutPlanContractError error)
        { error = new DrawingLayoutPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }

    internal sealed class DrawingLayoutVerificationInputs
    {
        public string OutputPath, ReportPath, ArtifactSha256, ExpectedLayoutFingerprint;
        public JObject ExpectedDimensionSemantics;
        public JArray ExpectedViewSemantics;
        public DimensionPlanExecutionPlan DimensionPlan;
        public int DimensionBaselineCount;
        public Dictionary<string, string> DimensionHandles, DimensionFingerprints;
    }
}
