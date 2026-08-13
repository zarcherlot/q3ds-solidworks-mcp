using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>COM-free F6 verification-sidecar, artifact and frozen-input gate.</summary>
    internal sealed class DimensionPlanVerificationPreflight
    {
        public bool TryValidate(DimensionPlanExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath,
            out DimensionPlanVerificationInputs inputs,
            out DimensionPlanContractError error)
        {
            inputs = null; error = null;
            string probeOutput;
            try
            {
                string directory = Path.GetDirectoryName(Path.GetFullPath(requestedOutputPath));
                probeOutput = Path.Combine(directory, ".q3ds-dimension-verify-" +
                    Guid.NewGuid().ToString("N") + ".SLDDRW");
            }
            catch (Exception ex)
            {
                return Fail("DIMENSION_OUTPUT_PATH_INVALID", "/output_path", ex.Message,
                    out error);
            }
            DimensionPlanTransactionPaths ignored;
            if (!new DimensionPlanTransactionPreflight().TryValidate(plan, planPath,
                planSha256, probeOutput, out ignored, out error))
                return false;

            string outputPath;
            try
            {
                outputPath = Path.GetFullPath(requestedOutputPath);
            }
            catch (Exception ex)
            {
                return Fail("DIMENSION_OUTPUT_PATH_INVALID", "/output_path", ex.Message,
                    out error);
            }
            if (!outputPath.EndsWith(".SLDDRW", StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(outputPath))
                return Fail("DIMENSION_OUTPUT_MISSING", "/output_path",
                    "Independent verification requires an existing .SLDDRW output.", out error);
            string reportPath = outputPath + ".dimension-verification.json";
            if (new[] { ignored.PlanPath, plan.Handoff.Path, plan.SourceModel.Path,
                    plan.SourceDrawing.Path, plan.ViewPlan.Path,
                    plan.VerificationSidecar.Path }.Any(path =>
                    PathEquals(path, outputPath) || PathEquals(path, reportPath)))
                return Fail("DIMENSION_OUTPUT_ALIASES_INPUT", "/output_path",
                    "The committed drawing or sidecar aliases a frozen input.", out error);
            if (!File.Exists(reportPath))
                return Fail("DIMENSION_VERIFICATION_REPORT_MISSING", "/output_path",
                    "The dimension verification sidecar is missing.", out error);

            JObject report;
            try { report = JObject.Parse(File.ReadAllText(reportPath)); }
            catch (Exception ex)
            {
                return Fail("DIMENSION_VERIFICATION_REPORT_INVALID", "/output_path",
                    ex.Message, out error);
            }
            if (report.Value<string>("protocol_id") !=
                    "solidworks-dimension-drawing-verification" ||
                report.Value<string>("schema_version") != "1.0" ||
                report.Value<bool?>("verified") != true ||
                report.Value<string>("plan_id") != plan.PlanId ||
                !PathEquals(report.Value<string>("plan_file_path"), ignored.PlanPath) ||
                !HashEquals(report.Value<string>("plan_file_sha256"), ignored.PlanFileSha256) ||
                !HashEquals(report.Value<string>("plan_canonical_sha256"), plan.PlanSha256) ||
                !PathEquals(report.Value<string>("output_path"), outputPath))
                return Fail("DIMENSION_VERIFICATION_REPORT_MISMATCH", "/output_path",
                    "The sidecar protocol or immutable plan/output binding is invalid.", out error);

            string artifactSha256 = DimensionPlanContractValidator.FileSha256(outputPath);
            if (!HashEquals(report.Value<string>("artifact_sha256"), artifactSha256))
                return Fail("DIMENSION_OUTPUT_HASH_MISMATCH", "/output_path",
                    "The output drawing SHA-256 differs from the committed sidecar.", out error);
            if (!ValidateFrozenInputs(report["frozen_inputs"] as JObject, plan,
                ignored.PlanPath, out error)) return false;

            JObject handlesObject = report["dimension_handles"] as JObject;
            JObject reopen = report["reopen_verification"] as JObject;
            JArray rows = reopen != null ? reopen["dimensions"] as JArray : null;
            if (handlesObject == null || reopen == null || rows == null ||
                reopen.Value<bool?>("verified") != true ||
                reopen.Value<int?>("planned_count") != plan.Dimensions.Count)
                return Fail("DIMENSION_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The sidecar lacks a verified persisted dimension snapshot.", out error);
            var expectedIds = new HashSet<string>(
                plan.Dimensions.Select(item => item.DimensionId), StringComparer.Ordinal);
            if (!expectedIds.SetEquals(handlesObject.Properties().Select(item => item.Name)) ||
                rows.Count != expectedIds.Count)
                return Fail("DIMENSION_VERIFICATION_REPORT_INVALID", "/output_path",
                    "Dimension handles/snapshot do not exactly cover DimensionPlan.", out error);

            var handles = new Dictionary<string, string>(StringComparer.Ordinal);
            var fingerprints = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string id in expectedIds)
            {
                string handle = handlesObject.Value<string>(id);
                JObject[] matches = rows.OfType<JObject>().Where(row =>
                    row.Value<string>("dimension_id") == id).ToArray();
                if (String.IsNullOrWhiteSpace(handle) || matches.Length != 1)
                    return Fail("DIMENSION_VERIFICATION_REPORT_INVALID", "/output_path",
                        "A dimension handle or persisted record is missing: " + id, out error);
                JObject row = matches[0];
                JArray holeVariables = row["hole_callout_variables"] as JArray;
                if (row.Value<string>("selection_name") != handle ||
                    row["text"] == null || holeVariables == null ||
                    row.Property("tolerance") == null)
                    return Fail("DIMENSION_VERIFICATION_REPORT_INVALID", "/output_path",
                        "An advanced persisted fingerprint is incomplete: " + id, out error);
                handles[id] = handle;
                fingerprints[id] = new JObject
                {
                    ["text"] = row["text"].DeepClone(),
                    ["hole_callout_variables"] = holeVariables.DeepClone(),
                    ["tolerance"] = row["tolerance"].DeepClone()
                }.ToString(Newtonsoft.Json.Formatting.None);
            }
            int? baselineCount = reopen.Value<int?>("baseline_count");
            if (baselineCount == null || baselineCount < 0)
                return Fail("DIMENSION_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The persisted baseline dimension count is invalid.", out error);
            inputs = new DimensionPlanVerificationInputs
            {
                OutputPath = outputPath, ReportPath = reportPath,
                ArtifactSha256 = artifactSha256, BaselineCount = baselineCount.Value,
                ExpectedHandles = handles, ExpectedFingerprints = fingerprints
            };
            return true;
        }

        private static bool ValidateFrozenInputs(JObject frozen,
            DimensionPlanExecutionPlan plan, string planPath,
            out DimensionPlanContractError error)
        {
            error = null;
            var expected = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["dimension_plan"] = planPath, ["handoff"] = plan.Handoff.Path,
                ["source_model"] = plan.SourceModel.Path,
                ["source_drawing"] = plan.SourceDrawing.Path,
                ["view_plan"] = plan.ViewPlan.Path,
                ["verification_sidecar"] = plan.VerificationSidecar.Path
            };
            if (frozen == null || !new HashSet<string>(frozen.Properties().Select(item =>
                item.Name), StringComparer.Ordinal).SetEquals(expected.Keys))
                return Fail("DIMENSION_FROZEN_INPUT_MISMATCH", "/output_path",
                    "The sidecar frozen-input inventory is incomplete.", out error);
            foreach (var pair in expected)
                if (!HashEquals(frozen.Value<string>(pair.Key),
                    DimensionPlanContractValidator.FileSha256(pair.Value)))
                    return Fail("DIMENSION_FROZEN_INPUT_MISMATCH", "/output_path",
                        "A frozen input differs from the committed sidecar: " + pair.Key,
                        out error);
            return true;
        }

        private static bool PathEquals(string first, string second)
        {
            try { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }
        private static bool HashEquals(string first, string second) =>
            !String.IsNullOrEmpty(first) && String.Equals(first, second,
                StringComparison.OrdinalIgnoreCase);
        private static bool Fail(string code, string pointer, string message,
            out DimensionPlanContractError error)
        { error = new DimensionPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }

    internal sealed class DimensionPlanVerificationInputs
    {
        public string OutputPath, ReportPath, ArtifactSha256;
        public int BaselineCount;
        public Dictionary<string, string> ExpectedHandles, ExpectedFingerprints;
    }
}
