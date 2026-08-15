using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>Strict COM-free boundary for the read-only G0 layout probe.</summary>
    public sealed class LayoutBoundaryProbeContract
    {
        public const string ProtocolId = "solidworks-layout-boundary-probe";
        public const string SchemaVersion = "1.0";
        public const string RequiredSolidWorksRevision = "33.5.0";

        public static readonly string[] CapabilityIds =
        {
            "view_outline_bounds",
            "dimension_display_bounds",
            "note_text_bounds",
            "leader_bounds",
            "view_label_bounds",
            "section_symbol_bounds",
            "center_element_bounds",
            "sheet_border_bounds",
            "title_block_bounds",
            "rebuild_drift",
            "save_reopen_drift"
        };

        private static readonly Regex Sha256 = new Regex(
            "^[0-9a-f]{64}$", RegexOptions.CultureInvariant);

        public bool TryParse(JToken candidate, out LayoutBoundaryProbeRequest request,
            out LayoutBoundaryProbeContractError error)
        {
            request = null;
            error = null;
            var root = candidate as JObject;
            if (root == null)
                return Fail("", "request must be a JSON object", out error);
            string[] exact = { "protocol_id", "schema_version", "source",
                "publication_directory", "required_solidworks_revision",
                "error_budget_m", "capability_ids" };
            if (!HasExactProperties(root, exact, "", out error)) return false;
            if (!String.Equals(root.Value<string>("protocol_id"), ProtocolId,
                    StringComparison.Ordinal))
                return Fail("/protocol_id", "unexpected protocol_id", out error);
            if (!String.Equals(root.Value<string>("schema_version"), SchemaVersion,
                    StringComparison.Ordinal))
                return Fail("/schema_version", "unexpected schema_version", out error);
            if (!String.Equals(root.Value<string>("required_solidworks_revision"),
                    RequiredSolidWorksRevision, StringComparison.Ordinal))
                return Fail("/required_solidworks_revision",
                    "G0 evidence is locked to SolidWorks 2025 SP5 revision 33.5.0",
                    out error);
            double? errorBudget = root.Value<double?>("error_budget_m");
            if (!errorBudget.HasValue || errorBudget.Value <= 0.0 ||
                errorBudget.Value > 0.005 || Double.IsNaN(errorBudget.Value) ||
                Double.IsInfinity(errorBudget.Value))
                return Fail("/error_budget_m",
                    "error_budget_m must be finite, positive, and at most 0.005 m",
                    out error);

            var source = root["source"] as JObject;
            if (source == null)
                return Fail("/source", "source must be an object", out error);
            string sourceKind = source.Value<string>("kind");
            bool dimensionSource = String.Equals(sourceKind,
                "verified_dimension_drawing", StringComparison.Ordinal);
            bool viewSource = String.Equals(sourceKind,
                "verified_view_plan_drawing", StringComparison.Ordinal);
            bool fixtureSource = String.Equals(sourceKind,
                "verified_layout_fixture", StringComparison.Ordinal);
            if (!dimensionSource && !viewSource && !fixtureSource)
                return Fail("/source/kind", "unexpected G0 source kind", out error);
            string planName = dimensionSource ? "dimension_plan" :
                viewSource ? "view_plan" : "layout_fixture_manifest";
            string drawingName = dimensionSource ? "dimensioned_drawing" :
                viewSource ? "view_drawing" : "fixture_drawing";
            string sidecarName = dimensionSource
                ? "dimension_verification_sidecar" : viewSource
                ? "view_verification_sidecar" : "source_verification_sidecar";
            if (!HasExactProperties(source, new[] { "kind", planName, drawingName,
                    sidecarName }, "/source", out error)) return false;

            LayoutBoundaryArtifact plan;
            LayoutBoundaryArtifact drawing;
            LayoutBoundaryArtifact sidecar;
            if (!TryArtifact(source[planName], "/source/" + planName,
                    ".json", out plan, out error) ||
                !TryArtifact(source[drawingName], "/source/" + drawingName,
                    ".SLDDRW", out drawing, out error) ||
                !TryArtifact(source[sidecarName], "/source/" + sidecarName,
                    ".json", out sidecar, out error)) return false;
            string[] sourcePaths = { plan.Path, drawing.Path, sidecar.Path };
            if (sourcePaths.Distinct(StringComparer.OrdinalIgnoreCase).Count() != 3)
                return Fail("/source", "source artifact paths must be distinct", out error);

            string publication;
            if (!TryAbsolutePath(root["publication_directory"],
                    "/publication_directory", out publication, out error)) return false;
            if (ContainsDirectorySegment(publication, "validation"))
                return Fail("/publication_directory",
                    "validation is read-only and cannot contain probe output", out error);
            foreach (string sourcePath in sourcePaths)
            {
                string sourceDirectory = Path.GetDirectoryName(sourcePath);
                if (String.Equals(publication, sourcePath,
                        StringComparison.OrdinalIgnoreCase) ||
                    String.Equals(publication, sourceDirectory,
                        StringComparison.OrdinalIgnoreCase) ||
                    publication.StartsWith(sourcePath + Path.DirectorySeparatorChar,
                        StringComparison.OrdinalIgnoreCase))
                    return Fail("/publication_directory",
                        "publication directory must not overwrite upstream artifacts",
                        out error);
            }

            var ids = root["capability_ids"] as JArray;
            if (ids == null || ids.Count != CapabilityIds.Length ||
                !ids.Select(item => item.Type == JTokenType.String
                        ? item.Value<string>() : null)
                    .SequenceEqual(CapabilityIds, StringComparer.Ordinal))
                return Fail("/capability_ids",
                    "capability_ids must match the complete frozen G0 catalog and order",
                    out error);

            request = new LayoutBoundaryProbeRequest
            {
                SourceKind = sourceKind,
                Plan = plan,
                Drawing = drawing,
                VerificationSidecar = sidecar,
                PublicationDirectory = publication,
                ErrorBudgetMeters = errorBudget.Value,
                CapabilityIds = (string[])CapabilityIds.Clone()
            };
            return true;
        }

        public bool TryPreflight(LayoutBoundaryProbeRequest request,
            out LayoutBoundaryProbeContractError error)
        {
            error = null;
            if (request == null) return Fail("", "parsed request is required", out error);
            foreach (LayoutBoundaryArtifact artifact in new[] { request.Plan,
                request.Drawing, request.VerificationSidecar })
            {
                if (artifact == null || !File.Exists(artifact.Path))
                    return Fail("/source", "source artifact does not exist: " +
                        (artifact == null ? "<null>" : artifact.Path), out error);
                string actual;
                using (var stream = File.OpenRead(artifact.Path))
                using (var algorithm = SHA256.Create())
                    actual = String.Concat(algorithm.ComputeHash(stream)
                        .Select(value => value.ToString("x2")));
                if (!String.Equals(actual, artifact.Sha256,
                        StringComparison.OrdinalIgnoreCase))
                    return Fail("/source", "source artifact SHA-256 mismatch: " +
                        artifact.Path, out error);
            }
            if (File.Exists(request.PublicationDirectory))
                return Fail("/publication_directory",
                    "publication directory resolves to a file", out error);
            if (Directory.Exists(request.PublicationDirectory) &&
                Directory.EnumerateFileSystemEntries(request.PublicationDirectory).Any())
                return Fail("/publication_directory",
                    "publication directory must be new or empty", out error);
            if (String.Equals(request.SourceKind, "verified_dimension_drawing",
                    StringComparison.Ordinal))
            {
                if (!TryVerifyDimensionSidecar(request, out error)) return false;
            }
            else if (String.Equals(request.SourceKind, "verified_view_plan_drawing",
                    StringComparison.Ordinal))
            {
                if (!TryVerifyViewSidecar(request, out error)) return false;
            }
            else if (!TryVerifyLayoutFixture(request, out error)) return false;
            return true;
        }

        private static bool TryVerifyDimensionSidecar(LayoutBoundaryProbeRequest request,
            out LayoutBoundaryProbeContractError error)
        {
            JObject sidecar;
            JObject plan;
            try
            {
                sidecar = JObject.Parse(File.ReadAllText(
                    request.VerificationSidecar.Path));
                plan = JObject.Parse(File.ReadAllText(request.Plan.Path));
            }
            catch (Exception exception)
            {
                return Fail("/source/dimension_verification_sidecar",
                    "dimension verification JSON could not be parsed: " +
                    exception.Message, out error);
            }
            bool protocol = String.Equals(sidecar.Value<string>("protocol_id"),
                    "solidworks-dimension-drawing-verification",
                    StringComparison.Ordinal) &&
                String.Equals(sidecar.Value<string>("schema_version"), "1.0",
                    StringComparison.Ordinal) &&
                sidecar.Value<bool?>("verified") == true;
            if (!protocol)
                return Fail("/source/dimension_verification_sidecar",
                    "sidecar is not a verified dimension drawing 1.0 report", out error);
            string sidecarPlanPath = sidecar.Value<string>("plan_file_path");
            string sidecarDrawingPath = sidecar.Value<string>("output_path");
            try
            {
                sidecarPlanPath = Path.GetFullPath(sidecarPlanPath ?? "");
                sidecarDrawingPath = Path.GetFullPath(sidecarDrawingPath ?? "");
            }
            catch
            {
                return Fail("/source/dimension_verification_sidecar",
                    "sidecar contains an invalid bound artifact path", out error);
            }
            if (!String.Equals(sidecarPlanPath, request.Plan.Path,
                    StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(sidecarDrawingPath, request.Drawing.Path,
                    StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(sidecar.Value<string>("plan_file_sha256"),
                    request.Plan.Sha256, StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(sidecar.Value<string>("artifact_sha256"),
                    request.Drawing.Sha256, StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(sidecar.SelectToken("frozen_inputs.dimension_plan")
                        ?.Value<string>(), request.Plan.Sha256,
                    StringComparison.OrdinalIgnoreCase) ||
                sidecar.SelectToken("in_memory_verification.verified")
                        ?.Value<bool?>() != true ||
                sidecar.SelectToken("reopen_verification.verified")
                        ?.Value<bool?>() != true ||
                !String.Equals(sidecar.Value<string>("plan_id"),
                    plan.Value<string>("plan_id"), StringComparison.Ordinal))
                return Fail("/source/dimension_verification_sidecar",
                    "sidecar does not bind the supplied verified DimensionPlan and drawing",
                    out error);
            error = null;
            return true;
        }

        private static bool TryVerifyViewSidecar(LayoutBoundaryProbeRequest request,
            out LayoutBoundaryProbeContractError error)
        {
            JObject sidecar;
            JObject plan;
            try
            {
                sidecar = JObject.Parse(File.ReadAllText(request.VerificationSidecar.Path));
                plan = JObject.Parse(File.ReadAllText(request.Plan.Path));
            }
            catch (Exception exception)
            {
                return Fail("/source/view_verification_sidecar",
                    "view verification JSON could not be parsed: " + exception.Message,
                    out error);
            }
            bool verified = String.Equals(sidecar.Value<string>("schema_version"), "1.0",
                    StringComparison.Ordinal) &&
                sidecar.Value<bool?>("verified") == true &&
                sidecar.SelectToken("verification.verified")?.Value<bool?>() == true;
            string planHash = CanonicalSha256(plan);
            if (!verified ||
                !String.Equals(sidecar.Value<string>("plan_id"),
                    plan.Value<string>("plan_id"), StringComparison.Ordinal) ||
                !String.Equals(sidecar.Value<string>("plan_canonical_sha256"), planHash,
                    StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(sidecar.Value<string>("artifact_sha256"),
                    request.Drawing.Sha256, StringComparison.OrdinalIgnoreCase) ||
                !(sidecar.SelectToken("verification.views") is JArray views) ||
                views.Count == 0)
                return Fail("/source/view_verification_sidecar",
                    "sidecar does not bind the supplied verified ViewPlan and drawing",
                    out error);
            error = null;
            return true;
        }

        private static bool TryVerifyLayoutFixture(LayoutBoundaryProbeRequest request,
            out LayoutBoundaryProbeContractError error)
        {
            JObject manifest;
            try
            {
                manifest = JObject.Parse(File.ReadAllText(request.Plan.Path));
            }
            catch (Exception exception)
            {
                return Fail("/source/layout_fixture_manifest",
                    "layout fixture manifest could not be parsed: " + exception.Message,
                    out error);
            }
            JArray before = manifest.SelectToken("title_block.before_extents_m") as JArray;
            JArray reopened = manifest.SelectToken("title_block.reopen_extents_m") as JArray;
            bool stable = before != null && reopened != null && before.Count == 4 &&
                reopened.Count == 4 && before.Select(item => item.Value<double>())
                    .SequenceEqual(reopened.Select(item => item.Value<double>()));
            if (!String.Equals(manifest.Value<string>("protocol_id"),
                    "solidworks-layout-g0-title-block-fixture", StringComparison.Ordinal) ||
                !String.Equals(manifest.Value<string>("schema_version"), "1.0",
                    StringComparison.Ordinal) || manifest.Value<bool?>("verified") != true ||
                !String.Equals(manifest.Value<string>("solidworks_revision"),
                    RequiredSolidWorksRevision, StringComparison.Ordinal) ||
                !String.Equals(Path.GetFullPath(manifest.Value<string>("fixture_drawing_path") ?? ""),
                    request.Drawing.Path, StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(manifest.Value<string>("fixture_drawing_sha256"),
                    request.Drawing.Sha256, StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(manifest.Value<string>("source_verification_sidecar_sha256"),
                    request.VerificationSidecar.Sha256, StringComparison.OrdinalIgnoreCase) ||
                manifest.SelectToken("title_block.native_api")?.Value<string>() !=
                    "ITitleBlock.GetExtents" || !stable)
                return Fail("/source/layout_fixture_manifest",
                    "fixture manifest does not bind a stable native title block drawing",
                    out error);
            error = null;
            return true;
        }

        private static string CanonicalSha256(JToken value)
        {
            string canonical = Canonicalize(value).ToString(
                Newtonsoft.Json.Formatting.None);
            using (var algorithm = SHA256.Create())
                return String.Concat(algorithm.ComputeHash(
                    System.Text.Encoding.UTF8.GetBytes(canonical))
                    .Select(item => item.ToString("x2")));
        }

        private static JToken Canonicalize(JToken value)
        {
            if (value is JObject obj)
            {
                var result = new JObject();
                foreach (JProperty property in obj.Properties()
                    .OrderBy(item => item.Name, StringComparer.Ordinal))
                    result[property.Name] = Canonicalize(property.Value);
                return result;
            }
            if (value is JArray array)
                return new JArray(array.Select(Canonicalize));
            return value.DeepClone();
        }

        private static bool TryArtifact(JToken token, string pointer, string extension,
            out LayoutBoundaryArtifact artifact,
            out LayoutBoundaryProbeContractError error)
        {
            artifact = null;
            var value = token as JObject;
            if (value == null)
                return Fail(pointer, "artifact must be an object", out error);
            if (!HasExactProperties(value, new[] { "path", "sha256" },
                    pointer, out error)) return false;
            string path;
            if (!TryAbsolutePath(value["path"], pointer + "/path", out path, out error))
                return false;
            if (!String.Equals(Path.GetExtension(path), extension,
                    StringComparison.OrdinalIgnoreCase))
                return Fail(pointer + "/path", "unexpected artifact extension", out error);
            string hash = value.Value<string>("sha256");
            if (String.IsNullOrWhiteSpace(hash) || !Sha256.IsMatch(hash))
                return Fail(pointer + "/sha256",
                    "SHA-256 must be 64 lowercase hexadecimal characters", out error);
            artifact = new LayoutBoundaryArtifact { Path = path, Sha256 = hash };
            error = null;
            return true;
        }

        private static bool TryAbsolutePath(JToken token, string pointer,
            out string path, out LayoutBoundaryProbeContractError error)
        {
            path = token != null && token.Type == JTokenType.String
                ? token.Value<string>() : null;
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                return Fail(pointer, "path must be absolute", out error);
            try { path = Path.GetFullPath(path); }
            catch (Exception ex) { return Fail(pointer, ex.Message, out error); }
            error = null;
            return true;
        }

        private static bool HasExactProperties(JObject value, string[] names,
            string pointer, out LayoutBoundaryProbeContractError error)
        {
            var expected = new System.Collections.Generic.HashSet<string>(names,
                StringComparer.Ordinal);
            foreach (JProperty property in value.Properties())
                if (!expected.Contains(property.Name))
                    return Fail(pointer + "/" + property.Name,
                        "unknown property", out error);
            foreach (string name in names)
                if (value.Property(name, StringComparison.Ordinal) == null)
                    return Fail(pointer + "/" + name,
                        "required property is missing", out error);
            error = null;
            return true;
        }

        private static bool ContainsDirectorySegment(string path, string segment)
        {
            return Path.GetFullPath(path).Split(Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar).Any(item =>
                    String.Equals(item, segment, StringComparison.OrdinalIgnoreCase));
        }

        private static bool Fail(string pointer, string message,
            out LayoutBoundaryProbeContractError error)
        {
            error = new LayoutBoundaryProbeContractError
            {
                Code = "LAYOUT_BOUNDARY_PROBE_CONTRACT_INVALID",
                JsonPointer = pointer,
                Message = message
            };
            return false;
        }
    }

    public sealed class LayoutBoundaryProbeRequest
    {
        public string SourceKind { get; internal set; }
        public LayoutBoundaryArtifact Plan { get; internal set; }
        public LayoutBoundaryArtifact Drawing { get; internal set; }
        public LayoutBoundaryArtifact VerificationSidecar { get; internal set; }
        public string PublicationDirectory { get; internal set; }
        public double ErrorBudgetMeters { get; internal set; }
        public string[] CapabilityIds { get; internal set; }
    }

    public sealed class LayoutBoundaryArtifact
    {
        public string Path { get; internal set; }
        public string Sha256 { get; internal set; }
    }

    public sealed class LayoutBoundaryProbeContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
