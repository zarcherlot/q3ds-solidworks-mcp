using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Strict, COM-free boundary for the F0 native dimension API probe.  It is
    /// intentionally separate from ViewPlan and from the legacy coordinate-based
    /// drawing dimension verbs.
    /// </summary>
    public sealed class DimensionApiProbeContract
    {
        public const string ProtocolId = "solidworks-dimension-api-probe";
        public const string SchemaVersion = "1.0";
        public const string RequiredSolidWorksRevision = "33.5.0";

        public static readonly string[] CapabilityIds =
        {
            "model_dimension_import",
            "display_dimension_iteration",
            "attachment_persistent_reference",
            "annotation_position",
            "annotation_text_bounds",
            "linear_dimension",
            "diameter_dimension",
            "radius_dimension",
            "angular_dimension",
            "hole_callout",
            "chamfer_dimension",
            "dimension_tolerance",
            "dimension_prefix_suffix",
            "save_reopen_stable_identity"
        };

        private static readonly Regex Sha256 = new Regex(
            "^[0-9a-f]{64}$", RegexOptions.CultureInvariant);

        public bool TryParse(JToken candidate, out DimensionApiProbeRequest request,
            out DimensionApiProbeContractError error)
        {
            request = null;
            error = null;
            var root = candidate as JObject;
            if (root == null)
                return Fail("", "request must be a JSON object", out error);
            if (!HasExactProperties(root, new[]
                {
                    "protocol_id", "schema_version", "source",
                    "publication_directory", "required_solidworks_revision",
                    "capability_ids"
                }, "", out error))
                return false;
            if (!string.Equals(root.Value<string>("protocol_id"), ProtocolId,
                StringComparison.Ordinal))
                return Fail("/protocol_id", "unexpected protocol_id", out error);
            if (!string.Equals(root.Value<string>("schema_version"), SchemaVersion,
                StringComparison.Ordinal))
                return Fail("/schema_version", "unexpected schema_version", out error);
            if (!string.Equals(root.Value<string>("required_solidworks_revision"),
                RequiredSolidWorksRevision, StringComparison.Ordinal))
                return Fail("/required_solidworks_revision",
                    "F0 evidence is locked to SolidWorks 2025 SP5 revision 33.5.0", out error);

            string publicationDirectory;
            if (!TryAbsolutePath(root["publication_directory"],
                "/publication_directory", null, out publicationDirectory, out error))
                return false;

            var source = root["source"] as JObject;
            if (source == null)
                return Fail("/source", "source must be an object", out error);
            string sourceKind = source.Value<string>("kind");
            DimensionApiProbeArtifact sourceModel = null;
            DimensionApiProbeArtifact sourceDrawing = null;
            DimensionApiProbeArtifact drawingTemplate = null;
            DimensionApiProbeArtifact viewPlan = null;
            DimensionApiProbeArtifact drawing = null;
            DimensionApiProbeArtifact sidecar = null;
            string[] sourcePaths;
            if (string.Equals(sourceKind, "research_model_drawing_pair",
                StringComparison.Ordinal))
            {
                if (!HasExactProperties(source, new[]
                    { "kind", "source_model", "source_drawing", "drawing_template" },
                    "/source", out error) ||
                    !TryArtifact(source["source_model"], "/source/source_model",
                        ".SLDPRT", out sourceModel, out error) ||
                    !TryArtifact(source["source_drawing"], "/source/source_drawing",
                        ".SLDDRW", out sourceDrawing, out error) ||
                    !TryArtifact(source["drawing_template"], "/source/drawing_template",
                        ".DRWDOT", out drawingTemplate, out error))
                    return false;
                if (!string.Equals(
                        Path.GetFileNameWithoutExtension(sourceModel.Path),
                        Path.GetFileNameWithoutExtension(sourceDrawing.Path),
                        StringComparison.OrdinalIgnoreCase) ||
                    !string.Equals(
                        Path.GetDirectoryName(sourceModel.Path),
                        Path.GetDirectoryName(sourceDrawing.Path),
                        StringComparison.OrdinalIgnoreCase))
                    return Fail("/source",
                        "research model and drawing must be an exact-basename pair in the same directory",
                        out error);
                sourcePaths = new[]
                    { sourceModel.Path, sourceDrawing.Path, drawingTemplate.Path };
            }
            else if (string.Equals(sourceKind, "frozen_viewplan_drawing",
                StringComparison.Ordinal))
            {
                if (!HasExactProperties(source, new[]
                    { "kind", "view_plan", "verified_drawing", "verification_sidecar" },
                    "/source", out error) ||
                    !TryArtifact(source["view_plan"], "/source/view_plan", ".json",
                        out viewPlan, out error) ||
                    !TryArtifact(source["verified_drawing"], "/source/verified_drawing",
                        ".SLDDRW", out drawing, out error) ||
                    !TryArtifact(source["verification_sidecar"],
                        "/source/verification_sidecar", ".json", out sidecar, out error))
                    return false;
                sourcePaths = new[] { viewPlan.Path, drawing.Path, sidecar.Path };
            }
            else
            {
                return Fail("/source/kind", "unknown F0 source kind", out error);
            }

            if (sourcePaths.Distinct(StringComparer.OrdinalIgnoreCase).Count() != sourcePaths.Length)
                return Fail("/source", "source artifact paths must be distinct", out error);
            string fullPublication = Path.GetFullPath(publicationDirectory);
            foreach (string sourcePath in sourcePaths)
            {
                string parent = Path.GetDirectoryName(sourcePath);
                if (string.Equals(sourcePath, fullPublication,
                        StringComparison.OrdinalIgnoreCase) ||
                    fullPublication.StartsWith(sourcePath + Path.DirectorySeparatorChar,
                        StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(parent, fullPublication, StringComparison.OrdinalIgnoreCase))
                    return Fail("/publication_directory",
                        "publication directory must not overwrite or contain upstream artifacts",
                        out error);
            }

            var capabilityArray = root["capability_ids"] as JArray;
            if (capabilityArray == null || capabilityArray.Count != CapabilityIds.Length)
                return Fail("/capability_ids",
                    "capability_ids must contain the complete F0 catalog", out error);
            var actual = capabilityArray.Select(item => item.Type == JTokenType.String
                ? item.Value<string>() : null).ToArray();
            if (!actual.SequenceEqual(CapabilityIds, StringComparer.Ordinal))
                return Fail("/capability_ids",
                    "capability_ids must match the frozen catalog and order", out error);

            request = new DimensionApiProbeRequest
            {
                SourceKind = sourceKind,
                SourceModel = sourceModel,
                SourceDrawing = sourceDrawing,
                DrawingTemplate = drawingTemplate,
                PublicationDirectory = fullPublication,
                ViewPlan = viewPlan,
                VerifiedDrawing = drawing,
                VerificationSidecar = sidecar,
                CapabilityIds = (string[])CapabilityIds.Clone()
            };
            return true;
        }

        public bool TryPreflight(DimensionApiProbeRequest request,
            out DimensionApiProbeContractError error)
        {
            error = null;
            if (request == null)
                return Fail("", "parsed request is required", out error);
            DimensionApiProbeArtifact[] artifacts =
                string.Equals(request.SourceKind, "research_model_drawing_pair",
                    StringComparison.Ordinal)
                ? new[]
                    { request.SourceModel, request.SourceDrawing, request.DrawingTemplate }
                : new[] { request.ViewPlan, request.VerifiedDrawing,
                    request.VerificationSidecar };
            foreach (DimensionApiProbeArtifact artifact in artifacts)
            {
                if (artifact == null || !File.Exists(artifact.Path))
                    return Fail("/source", "source artifact does not exist: " +
                        (artifact == null ? "<null>" : artifact.Path), out error);
                string actual;
                using (var stream = File.OpenRead(artifact.Path))
                using (var sha = SHA256.Create())
                    actual = string.Concat(sha.ComputeHash(stream)
                        .Select(value => value.ToString("x2")));
                if (!string.Equals(actual, artifact.Sha256,
                    StringComparison.OrdinalIgnoreCase))
                    return Fail("/source", "source artifact SHA-256 mismatch: " +
                        artifact.Path, out error);
            }
            if (Directory.Exists(request.PublicationDirectory) &&
                Directory.EnumerateFileSystemEntries(request.PublicationDirectory).Any())
                return Fail("/publication_directory",
                    "publication directory must be new or empty", out error);
            if (File.Exists(request.PublicationDirectory))
                return Fail("/publication_directory",
                    "publication directory resolves to a file", out error);
            return true;
        }

        private static bool TryArtifact(JToken token, string pointer, string extension,
            out DimensionApiProbeArtifact artifact,
            out DimensionApiProbeContractError error)
        {
            artifact = null;
            error = null;
            var value = token as JObject;
            if (value == null)
                return Fail(pointer, "artifact must be an object", out error);
            if (!HasExactProperties(value, new[] { "path", "sha256" }, pointer, out error))
                return false;
            string path;
            if (!TryAbsolutePath(value["path"], pointer + "/path", extension,
                out path, out error))
                return false;
            string hash = value.Value<string>("sha256");
            if (hash == null || !Sha256.IsMatch(hash))
                return Fail(pointer + "/sha256",
                    "sha256 must be 64 lowercase hexadecimal characters", out error);
            artifact = new DimensionApiProbeArtifact { Path = path, Sha256 = hash };
            return true;
        }

        private static bool TryAbsolutePath(JToken token, string pointer, string extension,
            out string path, out DimensionApiProbeContractError error)
        {
            path = token != null && token.Type == JTokenType.String
                ? token.Value<string>() : null;
            error = null;
            if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                return Fail(pointer, "path must be absolute", out error);
            try { path = Path.GetFullPath(path); }
            catch (Exception ex) { return Fail(pointer, ex.Message, out error); }
            if (extension != null && !string.Equals(Path.GetExtension(path), extension,
                StringComparison.OrdinalIgnoreCase))
                return Fail(pointer, "path must end with " + extension, out error);
            return true;
        }

        private static bool HasExactProperties(JObject value, IEnumerable<string> expected,
            string pointer, out DimensionApiProbeContractError error)
        {
            error = null;
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

        private static bool Fail(string pointer, string message,
            out DimensionApiProbeContractError error)
        {
            error = new DimensionApiProbeContractError
            {
                Code = "DIMENSION_API_PROBE_CONTRACT_INVALID",
                JsonPointer = pointer,
                Message = message
            };
            return false;
        }
    }

    public sealed class DimensionApiProbeRequest
    {
        public string SourceKind { get; internal set; }
        public DimensionApiProbeArtifact SourceModel { get; internal set; }
        public DimensionApiProbeArtifact SourceDrawing { get; internal set; }
        public DimensionApiProbeArtifact DrawingTemplate { get; internal set; }
        public string PublicationDirectory { get; internal set; }
        public DimensionApiProbeArtifact ViewPlan { get; internal set; }
        public DimensionApiProbeArtifact VerifiedDrawing { get; internal set; }
        public DimensionApiProbeArtifact VerificationSidecar { get; internal set; }
        public string[] CapabilityIds { get; internal set; }
    }

    public sealed class DimensionApiProbeArtifact
    {
        public string Path { get; internal set; }
        public string Sha256 { get; internal set; }
    }

    public sealed class DimensionApiProbeContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
