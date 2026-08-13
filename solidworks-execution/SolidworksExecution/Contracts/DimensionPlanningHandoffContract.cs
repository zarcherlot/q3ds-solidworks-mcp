using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Strict COM-free F1 boundary.  It binds one independently verified ViewPlan
    /// drawing before the execution service performs any read-only SolidWorks work.
    /// DimensionPlan is deliberately not part of this contract.
    /// </summary>
    public sealed class DimensionPlanningHandoffContract
    {
        public const string ProtocolId =
            "solidworks-dimension-planning-handoff-request";
        public const string SchemaVersion = "1.0";

        private static readonly Regex Sha256Pattern = new Regex(
            "^[0-9a-f]{64}$", RegexOptions.CultureInvariant);
        private static readonly Regex IdPattern = new Regex(
            "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", RegexOptions.CultureInvariant);
        private static readonly Regex Rfc3339DateTime = new Regex(
            @"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
            RegexOptions.CultureInvariant);

        public bool TryParse(JToken candidate,
            out DimensionPlanningHandoffRequest request,
            out DimensionPlanningHandoffContractError error)
        {
            request = null;
            error = null;
            var root = candidate as JObject;
            if (root == null)
                return Fail("", "request must be a JSON object", out error);
            if (!HasExactProperties(root, new[]
                {
                    "protocol_id", "schema_version", "source",
                    "publication_directory", "approved_user_inputs"
                }, "", out error)) return false;
            if (!String.Equals(root.Value<string>("protocol_id"), ProtocolId,
                    StringComparison.Ordinal))
                return Fail("/protocol_id", "unexpected protocol_id", out error);
            if (!String.Equals(root.Value<string>("schema_version"), SchemaVersion,
                    StringComparison.Ordinal))
                return Fail("/schema_version", "unexpected schema_version", out error);

            var source = root["source"] as JObject;
            if (source == null || !HasExactProperties(source, new[]
                { "view_plan", "verified_drawing", "verification_sidecar" },
                "/source", out error)) return false;
            DimensionPlanningArtifact viewPlan;
            DimensionPlanningArtifact drawing;
            DimensionPlanningArtifact sidecar;
            if (!TryArtifact(source["view_plan"], "/source/view_plan", ".json",
                    out viewPlan, out error) ||
                !TryArtifact(source["verified_drawing"],
                    "/source/verified_drawing", ".SLDDRW", out drawing, out error) ||
                !TryArtifact(source["verification_sidecar"],
                    "/source/verification_sidecar", ".json", out sidecar, out error))
                return false;
            if (new[] { viewPlan.Path, drawing.Path, sidecar.Path }
                .Distinct(StringComparer.OrdinalIgnoreCase).Count() != 3)
                return Fail("/source", "source artifact paths must be distinct", out error);

            string publication;
            if (!TryAbsolutePath(root["publication_directory"],
                    "/publication_directory", null, out publication, out error))
                return false;
            if (PathSegments(publication).Any(segment => String.Equals(segment,
                    "validation", StringComparison.OrdinalIgnoreCase)))
                return Fail("/publication_directory",
                    "publication directory must not be validation or one of its descendants",
                    out error);
            foreach (string sourcePath in new[]
                { viewPlan.Path, drawing.Path, sidecar.Path })
            {
                if (String.Equals(Path.GetDirectoryName(sourcePath), publication,
                        StringComparison.OrdinalIgnoreCase))
                    return Fail("/publication_directory",
                        "publication directory must differ from the upstream directory",
                        out error);
            }

            var inputs = root["approved_user_inputs"] as JArray;
            if (inputs == null)
                return Fail("/approved_user_inputs", "must be an array", out error);
            var inputIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < inputs.Count; index++)
            {
                var input = inputs[index] as JObject;
                string pointer = "/approved_user_inputs/" + index;
                if (!TryApprovedInput(input, pointer, inputIds, out error)) return false;
            }

            request = new DimensionPlanningHandoffRequest
            {
                ViewPlan = viewPlan,
                VerifiedDrawing = drawing,
                VerificationSidecar = sidecar,
                PublicationDirectory = publication,
                ApprovedUserInputs = (JArray)inputs.DeepClone(),
                SourceRequest = (JObject)root.DeepClone()
            };
            return true;
        }

        public bool TryPreflight(DimensionPlanningHandoffRequest request,
            out DimensionPlanningHandoffContractError error)
        {
            error = null;
            if (request == null)
                return Fail("", "parsed request is required", out error);
            foreach (DimensionPlanningArtifact artifact in new[]
                { request.ViewPlan, request.VerifiedDrawing,
                    request.VerificationSidecar })
            {
                if (artifact == null || !File.Exists(artifact.Path))
                    return Fail("/source", "source artifact does not exist: " +
                        (artifact == null ? "<null>" : artifact.Path), out error);
                string actual = FileSha256(artifact.Path);
                if (!String.Equals(actual, artifact.Sha256,
                        StringComparison.Ordinal))
                    return Fail("/source", "source artifact SHA-256 mismatch: " +
                        artifact.Path, out error);
            }
            if (File.Exists(request.PublicationDirectory) ||
                (Directory.Exists(request.PublicationDirectory) &&
                 Directory.EnumerateFileSystemEntries(request.PublicationDirectory).Any()))
                return Fail("/publication_directory",
                    "publication directory must be new or empty", out error);

            JObject plan;
            JObject sidecar;
            try
            {
                plan = ParseObjectFile(request.ViewPlan.Path);
                sidecar = ParseObjectFile(request.VerificationSidecar.Path);
            }
            catch (Exception exception)
            {
                return Fail("/source", "upstream JSON parse failed: " +
                    exception.Message, out error);
            }
            if (!String.Equals(plan.Value<string>("protocol_id"),
                    "solidworks-view-plan", StringComparison.Ordinal) ||
                !String.Equals(plan.Value<string>("schema_version"), "1.4",
                    StringComparison.Ordinal))
                return Fail("/source/view_plan",
                    "upstream plan must be solidworks-view-plan 1.4", out error);
            if (sidecar.Value<bool?>("verified") != true)
                return Fail("/source/verification_sidecar",
                    "verification sidecar is not marked verified", out error);
            string sidecarOutput;
            if (!TryAbsolutePath(sidecar["output_path"],
                    "/source/verification_sidecar/output_path", ".SLDDRW",
                    out sidecarOutput, out error)) return false;
            if (!PathEquals(sidecarOutput, request.VerifiedDrawing.Path) ||
                !String.Equals(sidecar.Value<string>("artifact_sha256"),
                    request.VerifiedDrawing.Sha256, StringComparison.Ordinal))
                return Fail("/source/verification_sidecar",
                    "sidecar drawing binding does not match the request", out error);
            string planCanonical = CanonicalSha256(plan);
            if (!String.Equals(sidecar.Value<string>("plan_canonical_sha256"),
                    planCanonical, StringComparison.Ordinal))
                return Fail("/source/verification_sidecar/plan_canonical_sha256",
                    "sidecar plan binding does not match the ViewPlan", out error);

            string modelPath;
            if (!TryAbsolutePath(plan["model_path"], "/source/view_plan/model_path",
                    ".SLDPRT", out modelPath, out error)) return false;
            if (!File.Exists(modelPath))
                return Fail("/source/view_plan/model_path",
                    "source model does not exist", out error);
            string modelHash = FileSha256(modelPath);
            if (!String.Equals(plan.Value<string>("model_sha256"), modelHash,
                    StringComparison.Ordinal))
                return Fail("/source/view_plan/model_sha256",
                    "ViewPlan model hash does not match the source model", out error);

            request.ViewPlanValue = plan;
            request.VerificationSidecarValue = sidecar;
            request.SourceModel = new DimensionPlanningArtifact
                { Path = modelPath, Sha256 = modelHash };
            request.PlanCanonicalSha256 = planCanonical;
            return true;
        }

        private static bool TryApprovedInput(JObject input, string pointer,
            ISet<string> ids, out DimensionPlanningHandoffContractError error)
        {
            error = null;
            if (input == null || !HasExactProperties(input, new[]
                {
                    "input_id", "source_tier", "approved_by", "approved_at_utc",
                    "approval_reference", "target_feature_ids", "value"
                }, pointer, out error)) return false;
            string id = input["input_id"] != null &&
                input["input_id"].Type == JTokenType.String
                ? input.Value<string>("input_id") : null;
            if (id == null || !IdPattern.IsMatch(id) || !ids.Add(id))
                return Fail(pointer + "/input_id",
                    "input_id must be unique and match the stable ID contract", out error);
            if (input["source_tier"].Type != JTokenType.String ||
                !String.Equals(input.Value<string>("source_tier"),
                    "user_confirmed_input", StringComparison.Ordinal))
                return Fail(pointer + "/source_tier",
                    "approved inputs must use user_confirmed_input", out error);
            foreach (string name in new[]
                { "approved_by", "approved_at_utc", "approval_reference" })
                if (input[name].Type != JTokenType.String ||
                    String.IsNullOrWhiteSpace(input.Value<string>(name)))
                    return Fail(pointer + "/" + name, "must be non-empty", out error);
            DateTimeOffset approvedAt;
            string approvedAtText = input.Value<string>("approved_at_utc");
            if (!Rfc3339DateTime.IsMatch(approvedAtText) ||
                !DateTimeOffset.TryParse(approvedAtText,
                    CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind,
                    out approvedAt))
                return Fail(pointer + "/approved_at_utc",
                    "must be an RFC 3339 date-time", out error);
            var targets = input["target_feature_ids"] as JArray;
            if (targets == null || targets.Any(item => item.Type != JTokenType.String ||
                    !IdPattern.IsMatch(item.Value<string>())))
                return Fail(pointer + "/target_feature_ids",
                    "must contain stable feature IDs", out error);
            if (targets.Select(item => item.Value<string>()).Distinct(
                    StringComparer.Ordinal).Count() != targets.Count)
                return Fail(pointer + "/target_feature_ids",
                    "feature IDs must be unique", out error);
            var value = input["value"] as JObject;
            if (value == null) return Fail(pointer + "/value",
                "value must be an object", out error);
            string kind = value.Value<string>("kind");
            if (String.Equals(kind, "quantity", StringComparison.Ordinal))
            {
                if (!HasExactProperties(value,
                    new[] { "kind", "quantity_kind", "value_si" },
                    pointer + "/value", out error)) return false;
                string quantity = value.Value<string>("quantity_kind");
                if (!(new[] { "length", "angle", "count" }).Contains(quantity,
                        StringComparer.Ordinal) || value["value_si"] == null ||
                    (value["value_si"].Type != JTokenType.Float &&
                     value["value_si"].Type != JTokenType.Integer))
                    return Fail(pointer + "/value",
                        "invalid approved quantity", out error);
                double number = value.Value<double>("value_si");
                if (Double.IsNaN(number) || Double.IsInfinity(number))
                    return Fail(pointer + "/value/value_si",
                        "quantity must be finite", out error);
            }
            else if (String.Equals(kind, "exact_text", StringComparison.Ordinal))
            {
                if (!HasExactProperties(value, new[] { "kind", "text" },
                    pointer + "/value", out error) ||
                    value["text"].Type != JTokenType.String ||
                    String.IsNullOrWhiteSpace(value.Value<string>("text")))
                    return Fail(pointer + "/value/text",
                        "approved text must be non-empty", out error);
            }
            else return Fail(pointer + "/value/kind",
                "unknown approved value kind", out error);
            return true;
        }

        private static bool TryArtifact(JToken token, string pointer,
            string extension, out DimensionPlanningArtifact artifact,
            out DimensionPlanningHandoffContractError error)
        {
            artifact = null;
            error = null;
            var value = token as JObject;
            if (value == null || !HasExactProperties(value,
                new[] { "path", "sha256" }, pointer, out error)) return false;
            string path;
            if (!TryAbsolutePath(value["path"], pointer + "/path", extension,
                    out path, out error)) return false;
            string hash = value.Value<string>("sha256");
            if (hash == null || !Sha256Pattern.IsMatch(hash))
                return Fail(pointer + "/sha256",
                    "sha256 must be 64 lowercase hexadecimal characters", out error);
            artifact = new DimensionPlanningArtifact { Path = path, Sha256 = hash };
            return true;
        }

        private static bool TryAbsolutePath(JToken token, string pointer,
            string extension, out string path,
            out DimensionPlanningHandoffContractError error)
        {
            path = token != null && token.Type == JTokenType.String
                ? token.Value<string>() : null;
            error = null;
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                return Fail(pointer, "path must be absolute", out error);
            if (path.IndexOfAny(new[] { '*', '?', '[', ']' }) >= 0)
                return Fail(pointer, "path must not contain wildcard characters", out error);
            try { path = Path.GetFullPath(path); }
            catch (Exception exception)
            { return Fail(pointer, exception.Message, out error); }
            if (extension != null && !String.Equals(Path.GetExtension(path),
                    extension, StringComparison.OrdinalIgnoreCase))
                return Fail(pointer, "path must end with " + extension, out error);
            return true;
        }

        private static JObject ParseObjectFile(string path)
        {
            return JObject.Parse(File.ReadAllText(path, Encoding.UTF8),
                new JsonLoadSettings
                {
                    DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                    CommentHandling = CommentHandling.Ignore,
                    LineInfoHandling = LineInfoHandling.Ignore
                });
        }

        public static string FileSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open,
                FileAccess.Read, FileShare.Read))
            using (var sha = SHA256.Create())
                return String.Concat(sha.ComputeHash(stream)
                    .Select(value => value.ToString("x2",
                        CultureInfo.InvariantCulture)));
        }

        public static string CanonicalSha256(JToken value)
        {
            string text = Canonicalize(value).ToString(Formatting.None);
            using (var sha = SHA256.Create())
                return String.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(text))
                    .Select(item => item.ToString("x2",
                        CultureInfo.InvariantCulture)));
        }

        private static JToken Canonicalize(JToken value)
        {
            var obj = value as JObject;
            if (obj != null)
            {
                var result = new JObject();
                foreach (JProperty property in obj.Properties().OrderBy(
                    item => item.Name, StringComparer.Ordinal))
                    result[property.Name] = Canonicalize(property.Value);
                return result;
            }
            var array = value as JArray;
            if (array != null) return new JArray(array.Select(Canonicalize));
            return value.DeepClone();
        }

        private static bool HasExactProperties(JObject value,
            IEnumerable<string> expected, string pointer,
            out DimensionPlanningHandoffContractError error)
        {
            error = null;
            if (value == null) return Fail(pointer, "must be an object", out error);
            var allowed = new HashSet<string>(expected, StringComparer.Ordinal);
            foreach (JProperty property in value.Properties())
                if (!allowed.Contains(property.Name))
                    return Fail(pointer + "/" + property.Name,
                        "unknown property", out error);
            foreach (string name in allowed)
                if (value[name] == null)
                    return Fail(pointer + "/" + name,
                        "required property is missing", out error);
            return true;
        }

        private static bool PathEquals(string first, string second)
        {
            try { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }

        private static IEnumerable<string> PathSegments(string path)
        {
            return (path ?? "").Split(new[]
                { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries);
        }

        private static bool Fail(string pointer, string message,
            out DimensionPlanningHandoffContractError error)
        {
            error = new DimensionPlanningHandoffContractError
            {
                Code = "DIMENSION_PLANNING_HANDOFF_CONTRACT_INVALID",
                JsonPointer = pointer ?? "",
                Message = message
            };
            return false;
        }
    }

    public sealed class DimensionPlanningHandoffRequest
    {
        public DimensionPlanningArtifact ViewPlan { get; internal set; }
        public DimensionPlanningArtifact VerifiedDrawing { get; internal set; }
        public DimensionPlanningArtifact VerificationSidecar { get; internal set; }
        public DimensionPlanningArtifact SourceModel { get; internal set; }
        public string PublicationDirectory { get; internal set; }
        public JArray ApprovedUserInputs { get; internal set; }
        public JObject SourceRequest { get; internal set; }
        public JObject ViewPlanValue { get; internal set; }
        public JObject VerificationSidecarValue { get; internal set; }
        public string PlanCanonicalSha256 { get; internal set; }
    }

    public sealed class DimensionPlanningArtifact
    {
        public string Path { get; internal set; }
        public string Sha256 { get; internal set; }
    }

    public sealed class DimensionPlanningHandoffContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
