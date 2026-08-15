using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Strict COM-free G1 boundary.  It accepts only an independently verified
    /// DimensionPlan drawing bound to the repository's live-complete G0 evidence.
    /// </summary>
    public sealed class LayoutPlanningHandoffContract
    {
        public const string ProtocolId =
            "solidworks-drawing-layout-handoff-request";
        public const string SchemaVersion = "1.0";
        private static readonly Regex Sha256 = new Regex(
            "^[0-9a-f]{64}$", RegexOptions.CultureInvariant);

        public bool TryParse(JToken candidate, out LayoutPlanningHandoffRequest request,
            out LayoutPlanningHandoffContractError error)
        {
            request = null;
            error = null;
            var root = candidate as JObject;
            if (root == null) return Fail("", "request must be a JSON object", out error);
            if (!HasExact(root, new[] { "protocol_id", "schema_version", "source",
                    "boundary_capabilities", "publication_directory",
                    "minimum_spacing_m" }, "", out error)) return false;
            if (!String.Equals(root.Value<string>("protocol_id"), ProtocolId,
                    StringComparison.Ordinal))
                return Fail("/protocol_id", "unexpected protocol_id", out error);
            if (!String.Equals(root.Value<string>("schema_version"), SchemaVersion,
                    StringComparison.Ordinal))
                return Fail("/schema_version", "unexpected schema_version", out error);

            var source = root["source"] as JObject;
            if (source == null || !HasExact(source, new[] { "dimension_plan",
                    "dimensioned_drawing", "dimension_verification_sidecar" },
                    "/source", out error)) return false;
            LayoutPlanningArtifact plan, drawing, sidecar;
            if (!TryArtifact(source["dimension_plan"], "/source/dimension_plan",
                    ".json", out plan, out error) ||
                !TryArtifact(source["dimensioned_drawing"],
                    "/source/dimensioned_drawing", ".SLDDRW", out drawing, out error) ||
                !TryArtifact(source["dimension_verification_sidecar"],
                    "/source/dimension_verification_sidecar", ".json",
                    out sidecar, out error)) return false;

            var capabilities = root["boundary_capabilities"] as JObject;
            if (capabilities == null || !HasExact(capabilities,
                    new[] { "manifest", "qualification" },
                    "/boundary_capabilities", out error)) return false;
            LayoutPlanningArtifact manifest, qualification;
            if (!TryArtifact(capabilities["manifest"],
                    "/boundary_capabilities/manifest", ".json", out manifest, out error) ||
                !TryArtifact(capabilities["qualification"],
                    "/boundary_capabilities/qualification", ".json",
                    out qualification, out error)) return false;
            var paths = new[] { plan.Path, drawing.Path, sidecar.Path, manifest.Path,
                qualification.Path };
            if (paths.Distinct(StringComparer.OrdinalIgnoreCase).Count() != paths.Length)
                return Fail("/source", "all frozen artifact paths must be distinct", out error);

            string publication;
            if (!TryAbsolutePath(root["publication_directory"],
                    "/publication_directory", null, out publication, out error)) return false;
            if (PathSegments(publication).Any(item => String.Equals(item, "validation",
                    StringComparison.OrdinalIgnoreCase)))
                return Fail("/publication_directory",
                    "validation and its descendants are read-only", out error);
            if (paths.Any(path => String.Equals(Path.GetDirectoryName(path), publication,
                    StringComparison.OrdinalIgnoreCase)))
                return Fail("/publication_directory",
                    "publication directory must differ from upstream directories", out error);

            var spacing = root["minimum_spacing_m"] as JObject;
            if (spacing == null || !HasExact(spacing, new[] { "object_to_object",
                    "object_to_frame", "text_to_geometry" },
                    "/minimum_spacing_m", out error)) return false;
            foreach (string name in new[] { "object_to_object", "object_to_frame",
                    "text_to_geometry" })
            {
                double? value = spacing.Value<double?>(name);
                if (!value.HasValue || value.Value < 0.0001 || value.Value > 0.02 ||
                    Double.IsNaN(value.Value) || Double.IsInfinity(value.Value))
                    return Fail("/minimum_spacing_m/" + name,
                        "spacing must be finite and between 0.0001 and 0.02 m", out error);
            }
            request = new LayoutPlanningHandoffRequest
            {
                DimensionPlan = plan,
                DimensionedDrawing = drawing,
                DimensionVerificationSidecar = sidecar,
                CapabilityManifest = manifest,
                BoundaryQualification = qualification,
                PublicationDirectory = publication,
                MinimumSpacing = (JObject)spacing.DeepClone(),
                SourceRequest = (JObject)root.DeepClone()
            };
            return true;
        }

        public bool TryPreflight(LayoutPlanningHandoffRequest request,
            out LayoutPlanningHandoffContractError error)
        {
            error = null;
            if (request == null) return Fail("", "parsed request is required", out error);
            foreach (LayoutPlanningArtifact artifact in request.Artifacts)
            {
                if (!File.Exists(artifact.Path))
                    return Fail("/source", "source artifact does not exist: " +
                        artifact.Path, out error);
                if (!String.Equals(DimensionPlanningHandoffContract.FileSha256(
                        artifact.Path), artifact.Sha256, StringComparison.Ordinal))
                    return Fail("/source", "source artifact SHA-256 mismatch: " +
                        artifact.Path, out error);
            }
            if (File.Exists(request.PublicationDirectory) ||
                (Directory.Exists(request.PublicationDirectory) &&
                 Directory.EnumerateFileSystemEntries(request.PublicationDirectory).Any()))
                return Fail("/publication_directory",
                    "publication directory must be new or empty", out error);

            JObject plan, sidecar, manifest, qualification;
            try
            {
                plan = ParseObjectFile(request.DimensionPlan.Path);
                sidecar = ParseObjectFile(request.DimensionVerificationSidecar.Path);
                manifest = ParseObjectFile(request.CapabilityManifest.Path);
                qualification = ParseObjectFile(request.BoundaryQualification.Path);
            }
            catch (Exception exception)
            {
                return Fail("/source", "frozen JSON parse failed: " +
                    exception.Message, out error);
            }
            if (plan.Value<string>("protocol_id") != "solidworks-dimension-plan" ||
                plan.Value<string>("schema_version") != "1.0")
                return Fail("/source/dimension_plan",
                    "upstream plan must be DimensionPlan 1.0", out error);
            bool sidecarVerified =
                sidecar.Value<string>("protocol_id") ==
                    "solidworks-dimension-drawing-verification" &&
                sidecar.Value<string>("schema_version") == "1.0" &&
                sidecar.Value<bool?>("verified") == true &&
                sidecar.SelectToken("in_memory_verification.verified")
                    ?.Value<bool?>() == true &&
                sidecar.SelectToken("reopen_verification.verified")
                    ?.Value<bool?>() == true;
            string sidecarPlan, sidecarDrawing;
            if (!sidecarVerified)
                return Fail("/source/dimension_verification_sidecar",
                    "sidecar is not independently verified", out error);
            if (!TryFullPath(sidecar.Value<string>("plan_file_path"),
                    out sidecarPlan) || !PathEquals(sidecarPlan,
                    request.DimensionPlan.Path))
                return Fail("/source/dimension_verification_sidecar/plan_file_path",
                    "sidecar plan path does not match", out error);
            if (!TryFullPath(sidecar.Value<string>("output_path"),
                    out sidecarDrawing) || !PathEquals(sidecarDrawing,
                    request.DimensionedDrawing.Path))
                return Fail("/source/dimension_verification_sidecar/output_path",
                    "sidecar drawing path does not match", out error);
            if (sidecar.Value<string>("plan_file_sha256") !=
                    request.DimensionPlan.Sha256)
                return Fail("/source/dimension_verification_sidecar/plan_file_sha256",
                    "sidecar plan file hash does not match", out error);
            if (sidecar.Value<string>("artifact_sha256") !=
                    request.DimensionedDrawing.Sha256)
                return Fail("/source/dimension_verification_sidecar/artifact_sha256",
                    "sidecar drawing hash does not match", out error);
            if (sidecar.Value<string>("plan_id") != plan.Value<string>("plan_id"))
                return Fail("/source/dimension_verification_sidecar/plan_id",
                    "sidecar plan ID does not match", out error);
            if (!Sha256.IsMatch(sidecar.Value<string>("plan_canonical_sha256") ?? ""))
                return Fail("/source/dimension_verification_sidecar/plan_canonical_sha256",
                    "sidecar canonical plan hash is invalid", out error);

            JArray planned = plan["dimensions"] as JArray;
            JArray reopened = sidecar.SelectToken("reopen_verification.dimensions") as JArray;
            if (planned == null || reopened == null || planned.Count == 0 ||
                planned.Count != reopened.Count ||
                !planned.Select(row => row.Value<string>("dimension_id"))
                    .OrderBy(value => value, StringComparer.Ordinal).SequenceEqual(
                    reopened.Select(row => row.Value<string>("dimension_id"))
                        .OrderBy(value => value, StringComparer.Ordinal),
                    StringComparer.Ordinal) ||
                reopened.Any(row => row["value_si"] == null ||
                    !(row["model_persistent_references"] is JArray references) ||
                    references.Count == 0))
                return Fail("/source/dimension_verification_sidecar",
                    "verified dimension values/attachments are incomplete or drifted", out error);

            var live = manifest["live_evidence"] as JObject;
            string boundQualification;
            bool liveComplete = manifest.Value<string>("protocol_id") ==
                    "solidworks-drawing-layout-executor-capabilities" &&
                manifest.Value<string>("schema_version") == "1.0" &&
                manifest.Value<string>("verification") == "live_complete" &&
                live != null && TryFullPath(live.Value<string>("qualification_path"),
                    out boundQualification) &&
                PathEquals(boundQualification, request.BoundaryQualification.Path) &&
                live.Value<string>("qualification_sha256") ==
                    request.BoundaryQualification.Sha256 &&
                qualification.Value<string>("protocol_id") ==
                    "solidworks-layout-g0-qualification" &&
                qualification.Value<string>("overall_status") == "complete" &&
                qualification.Value<string>("qualification_id") ==
                    live.Value<string>("qualification_id") &&
                qualification.Value<string>("solidworks_revision") ==
                    manifest.Value<string>("solidworks_revision");
            if (!liveComplete)
                return Fail("/boundary_capabilities",
                    "capability manifest is not bound to a complete G0 qualification", out error);

            request.DimensionPlanValue = plan;
            request.VerificationSidecarValue = sidecar;
            request.CapabilityManifestValue = manifest;
            request.QualificationValue = qualification;
            return true;
        }

        private static bool TryArtifact(JToken token, string pointer, string extension,
            out LayoutPlanningArtifact artifact,
            out LayoutPlanningHandoffContractError error)
        {
            artifact = null;
            var value = token as JObject;
            if (value == null)
                return Fail(pointer, "artifact must be an object", out error);
            if (!HasExact(value, new[] { "path", "sha256" },
                    pointer, out error)) return false;
            string path;
            if (!TryAbsolutePath(value["path"], pointer + "/path", extension,
                    out path, out error)) return false;
            string hash = value.Value<string>("sha256");
            if (hash == null || !Sha256.IsMatch(hash))
                return Fail(pointer + "/sha256",
                    "sha256 must be lowercase hexadecimal", out error);
            artifact = new LayoutPlanningArtifact { Path = path, Sha256 = hash };
            return true;
        }

        private static bool TryAbsolutePath(JToken token, string pointer,
            string extension, out string path,
            out LayoutPlanningHandoffContractError error)
        {
            path = token != null && token.Type == JTokenType.String
                ? token.Value<string>() : null;
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path) ||
                path.IndexOfAny(new[] { '*', '?', '[', ']' }) >= 0)
                return Fail(pointer, "path must be absolute and wildcard-free", out error);
            try { path = Path.GetFullPath(path); }
            catch (Exception exception) { return Fail(pointer, exception.Message, out error); }
            if (extension != null && !String.Equals(Path.GetExtension(path), extension,
                    StringComparison.OrdinalIgnoreCase))
                return Fail(pointer, "path must end with " + extension, out error);
            error = null;
            return true;
        }

        private static JObject ParseObjectFile(string path)
        {
            using (var reader = new JsonTextReader(new StringReader(
                File.ReadAllText(path, Encoding.UTF8)))
                { DateParseHandling = DateParseHandling.None })
                return JObject.Load(reader, new JsonLoadSettings
                {
                    DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error
                });
        }

        private static bool TryFullPath(string value, out string path)
        {
            path = null;
            if (String.IsNullOrWhiteSpace(value)) return false;
            try { path = Path.GetFullPath(value); return Path.IsPathRooted(path); }
            catch { return false; }
        }

        private static bool PathEquals(string first, string second)
        { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
            StringComparison.OrdinalIgnoreCase); }

        private static IEnumerable<string> PathSegments(string path)
        { return Path.GetFullPath(path).Split(Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar); }

        private static bool HasExact(JObject value, IEnumerable<string> names,
            string pointer, out LayoutPlanningHandoffContractError error)
        {
            if (value == null) return Fail(pointer, "must be an object", out error);
            var expected = new HashSet<string>(names, StringComparer.Ordinal);
            foreach (JProperty property in value.Properties())
                if (!expected.Contains(property.Name))
                    return Fail(pointer + "/" + property.Name,
                        "unknown property", out error);
            foreach (string name in expected)
                if (value[name] == null)
                    return Fail(pointer + "/" + name,
                        "required property is missing", out error);
            error = null;
            return true;
        }

        private static bool Fail(string pointer, string message,
            out LayoutPlanningHandoffContractError error)
        {
            error = new LayoutPlanningHandoffContractError
            {
                Code = "LAYOUT_PLANNING_HANDOFF_CONTRACT_INVALID",
                JsonPointer = pointer,
                Message = message
            };
            return false;
        }
    }

    public sealed class LayoutPlanningHandoffRequest
    {
        public LayoutPlanningArtifact DimensionPlan { get; internal set; }
        public LayoutPlanningArtifact DimensionedDrawing { get; internal set; }
        public LayoutPlanningArtifact DimensionVerificationSidecar { get; internal set; }
        public LayoutPlanningArtifact CapabilityManifest { get; internal set; }
        public LayoutPlanningArtifact BoundaryQualification { get; internal set; }
        public string PublicationDirectory { get; internal set; }
        public JObject MinimumSpacing { get; internal set; }
        public JObject SourceRequest { get; internal set; }
        public JObject DimensionPlanValue { get; internal set; }
        public JObject VerificationSidecarValue { get; internal set; }
        public JObject CapabilityManifestValue { get; internal set; }
        public JObject QualificationValue { get; internal set; }
        public IEnumerable<LayoutPlanningArtifact> Artifacts
        {
            get { return new[] { DimensionPlan, DimensionedDrawing,
                DimensionVerificationSidecar, CapabilityManifest,
                BoundaryQualification }; }
        }
    }

    public sealed class LayoutPlanningArtifact
    {
        public string Path { get; internal set; }
        public string Sha256 { get; internal set; }
    }

    public sealed class LayoutPlanningHandoffContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
