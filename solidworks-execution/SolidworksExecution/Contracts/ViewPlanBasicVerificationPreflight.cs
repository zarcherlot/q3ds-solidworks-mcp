using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// COM-free B4 gate for an already committed ViewPlan drawing. Revalidates all frozen inputs,
    /// the no-alias output path, the strict transaction sidecar, and the drawing hash before a
    /// read-only SolidWorks verification session is allowed to start.
    /// </summary>
    public sealed class ViewPlanBasicVerificationPreflight
    {
        private static readonly HashSet<string> AuditFields = new HashSet<string>(
            new[]
            {
                "schema_version", "operation_id", "generated_at_utc", "plan_id",
                "plan_canonical_sha256", "artifact_sha256", "output_path", "verified",
                "input_artifacts", "view_handles", "verification"
            }, StringComparer.Ordinal);

        public bool TryValidate(ViewPlanBasicExecutionPlan plan, string requestedOutputPath,
            out ViewPlanBasicVerificationInputs inputs,
            out ViewPlanExecutionContractError error)
        {
            inputs = null;
            error = null;
            if (!new ViewPlanBasicTransactionPreflight().TryValidateFrozenInputs(plan, out error))
                return false;

            string outputPath;
            try
            {
                if (string.IsNullOrWhiteSpace(requestedOutputPath) ||
                    !Path.IsPathRooted(requestedOutputPath))
                    throw new InvalidDataException("output_path must be absolute");
                outputPath = Path.GetFullPath(requestedOutputPath);
            }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_OUTPUT_PATH_INVALID", "/output_path", ex.Message,
                    out error);
            }
            if (!string.Equals(Path.GetExtension(outputPath), ".SLDDRW",
                StringComparison.OrdinalIgnoreCase))
                return Fail("VIEW_PLAN_OUTPUT_PATH_INVALID", "/output_path",
                    "output_path must end with .SLDDRW.", out error);
            if (plan.InputArtifacts.Any(item => PathEquals(item.Path, outputPath)))
                return Fail("VIEW_PLAN_OUTPUT_PATH_INVALID", "/output_path",
                    "output_path must differ from every frozen input artifact.", out error);
            if (!File.Exists(outputPath))
                return Fail("VIEW_PLAN_OUTPUT_NOT_FOUND", "/output_path",
                    "The committed drawing does not exist.", out error);
            string reportPath = outputPath + ".verification.json";
            if (!File.Exists(reportPath))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_NOT_FOUND", "/output_path",
                    "The transaction verification sidecar does not exist.", out error);

            JObject audit;
            try
            {
                using (var stream = File.OpenText(reportPath))
                using (var reader = new JsonTextReader(stream))
                {
                    reader.DateParseHandling = DateParseHandling.None;
                    audit = JObject.Load(reader, new JsonLoadSettings
                    {
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                        CommentHandling = CommentHandling.Ignore,
                        LineInfoHandling = LineInfoHandling.Ignore
                    });
                    if (reader.Read())
                        return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                            "The verification sidecar contains trailing JSON content.", out error);
                }
            }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    ex.Message, out error);
            }

            if (!AuditFields.SetEquals(audit.Properties().Select(item => item.Name)))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The verification sidecar fields do not match the B3 audit contract.",
                    out error);
            if (audit.Value<string>("schema_version") != "1.0" ||
                string.IsNullOrWhiteSpace(audit.Value<string>("operation_id")) ||
                audit.Value<string>("plan_id") != plan.PlanId ||
                !HashEquals(audit.Value<string>("plan_canonical_sha256"),
                    plan.PlanCanonicalSha256) ||
                !PathEquals(audit.Value<string>("output_path"), outputPath) ||
                audit["verified"] == null || audit["verified"].Type != JTokenType.Boolean ||
                !audit.Value<bool>("verified"))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_MISMATCH", "/output_path",
                    "The verification sidecar is not bound to this frozen plan and output.",
                    out error);
            DateTimeOffset generated;
            if (!DateTimeOffset.TryParseExact(audit.Value<string>("generated_at_utc"), "o",
                CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out generated))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "generated_at_utc must use the round-trip UTC timestamp format.", out error);
            string artifactSha256 = audit.Value<string>("artifact_sha256");
            if (!IsSha256(artifactSha256))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "artifact_sha256 is invalid.", out error);

            var auditArtifacts = audit["input_artifacts"] as JArray;
            if (auditArtifacts == null || auditArtifacts.Count != plan.InputArtifacts.Count)
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_MISMATCH", "/output_path",
                    "The sidecar input artifact inventory differs from the compiled plan.",
                    out error);
            var byRole = new Dictionary<string, JObject>(StringComparer.Ordinal);
            foreach (JObject row in auditArtifacts.OfType<JObject>())
            {
                if (row.Properties().Select(item => item.Name).OrderBy(item => item,
                    StringComparer.Ordinal).SequenceEqual(new[] { "path", "role", "sha256" }) ==
                    false || string.IsNullOrEmpty(row.Value<string>("role")) ||
                    byRole.ContainsKey(row.Value<string>("role")))
                    return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                        "The sidecar contains an invalid input artifact row.", out error);
                byRole.Add(row.Value<string>("role"), row);
            }
            if (byRole.Count != plan.InputArtifacts.Count)
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The sidecar contains a non-object input artifact row.", out error);
            foreach (ViewPlanBoundArtifact artifact in plan.InputArtifacts)
            {
                JObject row;
                if (!byRole.TryGetValue(artifact.Role, out row) ||
                    !PathEquals(row.Value<string>("path"), artifact.Path) ||
                    !HashEquals(row.Value<string>("sha256"), artifact.Sha256))
                    return Fail("VIEW_PLAN_VERIFICATION_REPORT_MISMATCH", "/output_path",
                        "The sidecar input artifact inventory differs from the frozen plan.",
                        out error);
            }

            var handlesObject = audit["view_handles"] as JObject;
            if (handlesObject == null || handlesObject.Count != plan.Views.Count)
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The sidecar view handle inventory is incomplete.", out error);
            var handles = new Dictionary<string, string>(StringComparer.Ordinal);
            var uniqueHandles = new HashSet<string>(StringComparer.Ordinal);
            foreach (ViewPlanBasicViewSpec view in plan.Views)
            {
                JToken value = handlesObject[view.Id];
                string handle = value != null && value.Type == JTokenType.String
                    ? value.Value<string>() : null;
                if (string.IsNullOrWhiteSpace(handle) || !uniqueHandles.Add(handle))
                    return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                        "Each planned view requires one distinct persistent handle.", out error);
                handles.Add(view.Id, handle);
            }
            if (handlesObject.Properties().Any(item => !handles.ContainsKey(item.Name)))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The sidecar contains an unknown view handle.", out error);
            var persisted = audit["verification"] as JObject;
            if (persisted == null || persisted["verified"] == null ||
                persisted["verified"].Type != JTokenType.Boolean ||
                !persisted.Value<bool>("verified"))
                return Fail("VIEW_PLAN_VERIFICATION_REPORT_INVALID", "/output_path",
                    "The sidecar does not contain a successful persisted verification snapshot.",
                    out error);

            string actualSha256;
            try { actualSha256 = ComputeFileSha256(outputPath); }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_OUTPUT_HASH_FAILED", "/output_path", ex.Message, out error);
            }
            if (!HashEquals(actualSha256, artifactSha256))
                return Fail("VIEW_PLAN_OUTPUT_HASH_MISMATCH", "/output_path",
                    "The drawing SHA-256 differs from its transaction sidecar.", out error);

            inputs = new ViewPlanBasicVerificationInputs
            {
                OutputPath = outputPath,
                ReportPath = reportPath,
                ArtifactSha256 = actualSha256,
                ExpectedHandles = handles
            };
            return true;
        }

        private static string ComputeFileSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
                FileShare.Read))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "")
                    .ToLowerInvariant();
        }

        private static bool IsSha256(string value)
        {
            return value != null && value.Length == 64 && value.All(character =>
                (character >= '0' && character <= '9') ||
                (character >= 'a' && character <= 'f') ||
                (character >= 'A' && character <= 'F'));
        }

        private static bool HashEquals(string first, string second)
        {
            return IsSha256(first) && string.Equals(first, second,
                StringComparison.OrdinalIgnoreCase);
        }

        private static bool PathEquals(string first, string second)
        {
            try
            {
                return !string.IsNullOrWhiteSpace(first) && !string.IsNullOrWhiteSpace(second) &&
                    string.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                        StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }

        private static bool Fail(string code, string pointer, string message,
            out ViewPlanExecutionContractError error)
        {
            error = new ViewPlanExecutionContractError
            {
                Code = code,
                JsonPointer = pointer,
                Message = message
            };
            return false;
        }
    }

    public sealed class ViewPlanBasicVerificationInputs
    {
        public string OutputPath { get; internal set; }
        public string ReportPath { get; internal set; }
        public string ArtifactSha256 { get; internal set; }
        public IDictionary<string, string> ExpectedHandles { get; internal set; }
    }
}
