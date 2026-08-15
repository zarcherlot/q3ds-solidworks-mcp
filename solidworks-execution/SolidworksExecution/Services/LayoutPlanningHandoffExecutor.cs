using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// G1 read-only transaction.  The handoff is the final and only published
    /// file, after close/rebuild/reopen readback and all upstream hash checks.
    /// </summary>
    internal sealed class LayoutPlanningHandoffExecutor
    {
        private const string OutputName = "drawing-layout-handoff.json";
        private readonly ISldWorks _solidWorks;

        public LayoutPlanningHandoffExecutor(ISldWorks solidWorks)
        { _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks"); }

        public JObject Execute(LayoutPlanningHandoffRequest request)
        {
            if (request == null) throw new ArgumentNullException("request");
            Directory.CreateDirectory(request.PublicationDirectory);
            string outputPath = Path.Combine(request.PublicationDirectory, OutputName);
            if (File.Exists(outputPath))
                throw new IOException("refusing to overwrite G1 handoff: " + outputPath);
            if (_solidWorks.GetOpenDocumentByName(
                    request.DimensionedDrawing.Path) != null)
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_DRAWING_ALREADY_OPEN: read-only ownership is ambiguous.");

            string requestHash = DimensionPlanningHandoffContract.CanonicalSha256(
                request.SourceRequest);
            var ledger = BuildLedger(request);
            IModelDoc2 model = null;
            JObject before = null, rebuilt = null, reopened = null;
            string revision = _solidWorks.RevisionNumber();
            if (!String.Equals(revision,
                    request.CapabilityManifestValue.Value<string>("solidworks_revision"),
                    StringComparison.Ordinal))
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_REVISION_MISMATCH: live SolidWorks differs from G0.");
            try
            {
                var reader = new LayoutBoundaryProbeExecutor(_solidWorks);
                model = reader.OpenReadOnly(request.DimensionedDrawing.Path);
                var drawing = model as IDrawingDoc;
                if (drawing == null || !model.IsOpenedReadOnly())
                    throw new InvalidOperationException(
                        "LAYOUT_HANDOFF_READONLY_OPEN_FAILED");
                before = LayoutBoundaryProbeExecutor.CaptureSnapshot(model, drawing,
                    "before_rebuild");
                LayoutBoundaryProbeExecutor.RebuildForReadback(model);
                rebuilt = LayoutBoundaryProbeExecutor.CaptureSnapshot(model, drawing,
                    "after_rebuild");
                _solidWorks.CloseDoc(model.GetTitle());
                model = null;

                model = reader.OpenReadOnly(request.DimensionedDrawing.Path);
                drawing = model as IDrawingDoc;
                if (drawing == null || !model.IsOpenedReadOnly())
                    throw new InvalidOperationException(
                        "LAYOUT_HANDOFF_READONLY_REOPEN_FAILED");
                LayoutBoundaryProbeExecutor.RebuildForReadback(model);
                reopened = LayoutBoundaryProbeExecutor.CaptureSnapshot(model, drawing,
                    "readonly_reopen");
                _solidWorks.CloseDoc(model.GetTitle());
                model = null;

                EnsureIdentityStable(before, rebuilt, reopened);
                CompleteLedger(ledger);
                EnsureLedgerUnchanged(ledger);

                JArray verifiedDimensions = request.VerificationSidecarValue
                    .SelectToken("reopen_verification.dimensions") as JArray;
                if (verifiedDimensions == null)
                    throw new InvalidOperationException(
                        "LAYOUT_HANDOFF_DIMENSION_READBACK_MISSING");
                JArray dimensions = BuildDimensionInvariants(
                    request.VerificationSidecarValue);
                JArray objects = BuildObjects(reopened,
                    request.CapabilityManifestValue, verifiedDimensions);
                JArray required = new JArray(objects.OfType<JObject>()
                    .Select(row => row.Value<string>("category"))
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(value => value, StringComparer.Ordinal));
                var supported = CapabilityStatuses(request.CapabilityManifestValue);
                JArray unsupported = new JArray(required.Values<string>().Where(id =>
                    !supported.ContainsKey(id) || supported[id] != "supported" ||
                    objects.OfType<JObject>().Any(row =>
                        row.Value<string>("category") == id &&
                        row.Value<bool>("exact") == false))
                    .Distinct(StringComparer.Ordinal)
                    .OrderBy(value => value, StringComparer.Ordinal));
                JArray blockers = new JArray(unsupported.Values<string>().Select(id =>
                    id + " is not qualified for exact collision boundaries"));

                int verifiedCount = request.VerificationSidecarValue
                    .SelectToken("reopen_verification.actual_total_count")
                    ?.Value<int>() ?? dimensions.Count;
                JObject sheet = BuildSheet(reopened, request.MinimumSpacing);
                JObject result = new JObject
                {
                    ["protocol_id"] = "solidworks-drawing-layout-handoff",
                    ["schema_version"] = "1.0",
                    ["handoff_id"] = "DLH-" + requestHash.Substring(0, 16),
                    ["created_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture),
                    ["status"] = unsupported.Count == 0 ? "ready" :
                        "capability_blocked",
                    ["source_request_sha256"] = requestHash,
                    ["upstream_artifacts"] = ledger,
                    ["dimension_semantics"] = new JObject
                    {
                        ["plan_id"] = request.DimensionPlanValue["plan_id"].DeepClone(),
                        ["planned_count"] = dimensions.Count,
                        ["verified_actual_count"] = verifiedCount,
                        ["invariant_sha256"] =
                            LayoutBoundaryProbeExecutor.CanonicalSha256(dimensions),
                        ["dimensions"] = dimensions
                    },
                    ["solidworks"] = new JObject
                    {
                        ["revision"] = revision,
                        ["execution_mode"] = "live_read_only"
                    },
                    ["sheet"] = sheet,
                    ["objects"] = objects,
                    ["constraints"] = BuildConstraints(reopened, sheet),
                    ["minimum_spacing_m"] = request.MinimumSpacing.DeepClone(),
                    ["boundary_capabilities"] = new JObject
                    {
                        ["registry_version"] = request.CapabilityManifestValue[
                            "registry_version"].DeepClone(),
                        ["verification"] = request.CapabilityManifestValue[
                            "verification"].DeepClone(),
                        ["qualification_id"] = request.QualificationValue[
                            "qualification_id"].DeepClone(),
                        ["required"] = required,
                        ["unsupported"] = unsupported
                    },
                    ["snapshots"] = new JObject
                    {
                        ["before_rebuild_sha256"] =
                            LayoutBoundaryProbeExecutor.CanonicalSha256(
                                before["objects"]),
                        ["after_rebuild_sha256"] =
                            LayoutBoundaryProbeExecutor.CanonicalSha256(
                                rebuilt["objects"]),
                        ["readonly_reopen_sha256"] =
                            LayoutBoundaryProbeExecutor.CanonicalSha256(
                                reopened["objects"]),
                        ["object_identity_stable"] = true
                    },
                    ["source_immutability"] = new JObject
                    {
                        ["drawing_opened_read_only"] = true,
                        ["drawing_saved"] = false,
                        ["hashes_unchanged"] = true,
                        ["dimension_count_unchanged"] = true,
                        ["dimension_values_unchanged"] = true,
                        ["dimension_attachments_unchanged"] = true
                    },
                    ["blockers"] = blockers
                };
                AtomicWriteJson(outputPath, result);
                return new JObject
                {
                    ["status"] = result["status"].DeepClone(),
                    ["handoff_path"] = outputPath,
                    ["handoff_sha256"] =
                        DimensionPlanningHandoffContract.FileSha256(outputPath),
                    ["handoff_id"] = result["handoff_id"].DeepClone(),
                    ["object_count"] = objects.Count,
                    ["dimension_count"] = dimensions.Count,
                    ["unsupported_capabilities"] = unsupported.DeepClone()
                };
            }
            finally
            {
                if (model != null)
                    try { _solidWorks.CloseDoc(model.GetTitle()); } catch { }
            }
        }

        private static JArray BuildDimensionInvariants(JObject sidecar)
        {
            var rows = sidecar.SelectToken("reopen_verification.dimensions") as JArray;
            return new JArray(rows.OfType<JObject>()
                .OrderBy(row => row.Value<string>("dimension_id"), StringComparer.Ordinal)
                .Select(row => new JObject
                {
                    ["dimension_id"] = row["dimension_id"].DeepClone(),
                    ["value_si"] = row["value_si"].DeepClone(),
                    ["model_persistent_references"] =
                        row["model_persistent_references"].DeepClone()
                }));
        }

        private static JArray BuildObjects(JObject snapshot, JObject manifest,
            JArray verifiedDimensions)
        {
            var statuses = CapabilityStatuses(manifest);
            JArray objects = new JArray(((JArray)snapshot["objects"]).OfType<JObject>()
                .OrderBy(row => row.Value<string>("id"), StringComparer.Ordinal)
                .Select(row =>
                {
                    var copy = (JObject)row.DeepClone();
                    string category = copy.Value<string>("category");
                    string sourceId = copy.Value<string>("id");
                    copy["source_id"] = sourceId;
                    copy["id"] = LayoutObjectId(sourceId);
                    copy["collision_usable"] = copy.Value<bool>("exact") &&
                        statuses.ContainsKey(category) && statuses[category] == "supported";
                    if (category == "dimension_display_bounds")
                    {
                        JObject dimension = MatchVerifiedDimension(copy, verifiedDimensions);
                        copy["dimension_id"] = dimension["dimension_id"].DeepClone();
                        copy["attachment_point_sheet_m"] =
                            dimension["position_sheet_m"].DeepClone();
                        copy["current_position_sheet_m"] =
                            dimension["position_sheet_m"].DeepClone();
                    }
                    return copy;
                }));
            int boundDimensionCount = objects.OfType<JObject>().Count(row =>
                row.Value<string>("category") == "dimension_display_bounds" &&
                row["dimension_id"] != null);
            if (boundDimensionCount != verifiedDimensions.Count)
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_DIMENSION_BOUNDARY_COUNT_MISMATCH");
            return objects;
        }

        private static JObject MatchVerifiedDimension(JObject boundary,
            JArray verifiedDimensions)
        {
            string sourceId = boundary.Value<string>("source_id");
            string view = boundary.Value<string>("view");
            string prefix = "dimension:" + view + ":";
            if (String.IsNullOrWhiteSpace(sourceId) ||
                !sourceId.StartsWith(prefix, StringComparison.Ordinal))
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_DIMENSION_BOUNDARY_ID_INVALID");
            string selectionName = sourceId.Substring(prefix.Length);
            JObject[] matches = verifiedDimensions.OfType<JObject>().Where(row =>
                row.Value<string>("view") == view &&
                row.Value<string>("selection_name") == selectionName &&
                row["dimension_id"] != null &&
                row["position_sheet_m"] is JArray &&
                ((JArray)row["position_sheet_m"]).Count >= 2).ToArray();
            if (matches.Length != 1)
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_DIMENSION_BOUNDARY_BINDING_AMBIGUOUS: " + sourceId);
            return matches[0];
        }

        private static Dictionary<string, string> CapabilityStatuses(JObject manifest)
        {
            return ((JArray)manifest["capabilities"]).OfType<JObject>()
                .ToDictionary(row => row.Value<string>("id"),
                    row => row.Value<string>("status"), StringComparer.Ordinal);
        }

        private static JObject BuildSheet(JObject snapshot, JObject spacing)
        {
            JObject border = ((JArray)snapshot["objects"]).OfType<JObject>()
                .Single(row => row.Value<string>("category") == "sheet_border_bounds");
            double[] bounds = border["bounds"].Values<double>().ToArray();
            double margin = spacing.Value<double>("object_to_frame");
            if (bounds[2] - bounds[0] <= 2 * margin ||
                bounds[3] - bounds[1] <= 2 * margin)
                throw new InvalidOperationException("LAYOUT_HANDOFF_SAFE_SHEET_EMPTY");
            return new JObject
            {
                ["name"] = snapshot.Value<string>("sheet_name"),
                ["bounds_m"] = new JArray(bounds),
                ["safe_bounds_m"] = new JArray(bounds[0] + margin,
                    bounds[1] + margin, bounds[2] - margin, bounds[3] - margin),
                ["scale_numerator"] = PositiveInteger(snapshot.SelectToken(
                    "sheet.scale_numerator"), 1),
                ["scale_denominator"] = PositiveInteger(snapshot.SelectToken(
                    "sheet.scale_denominator"), 1)
            };
        }

        private static JObject BuildConstraints(JObject snapshot, JObject sheet)
        {
            JObject[] snapshotViews = ((JArray)snapshot["views"]).OfType<JObject>().ToArray();
            double[] outer = sheet["bounds_m"].Values<double>().ToArray();
            double[] safe = sheet["safe_bounds_m"].Values<double>().ToArray();
            var zones = new JArray(
                Zone("sheet-frame-left", "sheet_frame",
                    new[] { outer[0], outer[1], safe[0], outer[3] }),
                Zone("sheet-frame-right", "sheet_frame",
                    new[] { safe[2], outer[1], outer[2], outer[3] }),
                Zone("sheet-frame-bottom", "sheet_frame",
                    new[] { safe[0], outer[1], safe[2], safe[1] }),
                Zone("sheet-frame-top", "sheet_frame",
                    new[] { safe[0], safe[3], safe[2], outer[3] }));
            foreach (JObject title in ((JArray)snapshot["objects"]).OfType<JObject>()
                .Where(row => row.Value<string>("category") == "title_block_bounds"))
                zones.Add(Zone("title-block", "title_block",
                    title["bounds"].Values<double>().ToArray()));

            var parentage = new JArray();
            var alignments = new JArray();
            var viewConstraints = new JArray();
            foreach (JObject view in snapshotViews)
            {
                int[] ratio = ReducedRatio(view["scale_ratio"] as JArray);
                viewConstraints.Add(new JObject
                {
                    ["view"] = view["name"].DeepClone(),
                    ["position_sheet_m"] = view["position_sheet_m"].DeepClone(),
                    ["position_locked"] = view["position_locked"].DeepClone(),
                    ["uses_sheet_scale"] = (view.Value<int?>("use_sheet_scale") ?? 1) != 0,
                    ["use_parent_scale"] = view["use_parent_scale"] != null
                        ? view["use_parent_scale"].DeepClone() : (JToken)false,
                    ["scale_numerator"] = ratio[0],
                    ["scale_denominator"] = ratio[1]
                });
                if (view["base_view"].Type == JTokenType.Null) continue;
                parentage.Add(new JObject
                {
                    ["view"] = view["name"].DeepClone(),
                    ["parent_view"] = view["base_view"].DeepClone()
                });
                if (view["projection_alignment"] != null)
                {
                    string parentName = view.Value<string>("base_view");
                    JObject parent = snapshotViews.Single(row =>
                        row.Value<string>("name") == parentName);
                    double[] childPosition = view["position_sheet_m"].Values<double>().ToArray();
                    double[] parentPosition = parent["position_sheet_m"].Values<double>().ToArray();
                    string axis = view.Value<string>("projection_alignment");
                    alignments.Add(new JObject
                    {
                        ["view"] = view["name"].DeepClone(),
                        ["parent_view"] = view["base_view"].DeepClone(),
                        ["axis"] = view["projection_alignment"].DeepClone(),
                        ["offset_m"] = Math.Round(axis == "horizontal"
                            ? childPosition[1] - parentPosition[1]
                            : childPosition[0] - parentPosition[0], 12)
                    });
                }
            }
            var frozen = new JArray(((JArray)snapshot["objects"]).OfType<JObject>()
                .Where(row => row.Value<string>("category") == "sheet_border_bounds" ||
                    row.Value<string>("category") == "title_block_bounds")
                .Select(row => LayoutObjectId(row.Value<string>("id"))));
            return new JObject
            {
                ["locked_zones"] = zones,
                ["frozen_objects"] = frozen,
                ["view_constraints"] = viewConstraints,
                ["view_parentage"] = parentage,
                ["projection_alignments"] = alignments
            };
        }

        private static int[] ReducedRatio(JArray raw)
        {
            double ratio = 1.0;
            if (raw != null && raw.Count >= 2)
            {
                double numerator = raw[0].Value<double>();
                double denominator = raw[1].Value<double>();
                if (numerator > 0.0 && denominator > 0.0)
                    ratio = numerator / denominator;
            }
            int selectedNumerator = 1;
            int selectedDenominator = 1;
            double selectedError = Math.Abs(ratio - 1.0);
            for (int denominator = 1; denominator <= 1000; denominator++)
            {
                int numerator = Convert.ToInt32(Math.Round(ratio * denominator));
                if (numerator < 1 || numerator > 1000) continue;
                double error = Math.Abs(ratio - (double)numerator / denominator);
                if (error < selectedError)
                {
                    selectedNumerator = numerator;
                    selectedDenominator = denominator;
                    selectedError = error;
                }
                if (error <= 1e-12) break;
            }
            int divisor = GreatestCommonDivisor(selectedNumerator, selectedDenominator);
            return new[] { selectedNumerator / divisor, selectedDenominator / divisor };
        }

        private static int GreatestCommonDivisor(int left, int right)
        {
            while (right != 0)
            {
                int remainder = left % right;
                left = right;
                right = remainder;
            }
            return Math.Abs(left);
        }

        private static JObject Zone(string id, string kind, double[] bounds)
        { return new JObject { ["zone_id"] = id, ["kind"] = kind,
            ["bounds_m"] = new JArray(bounds) }; }

        private static int PositiveInteger(JToken value, int fallback)
        {
            if (value == null) return fallback;
            double number = value.Value<double>();
            int rounded = (int)Math.Round(number);
            return rounded > 0 && Math.Abs(number - rounded) <= 1e-9 ? rounded : fallback;
        }

        private static string LayoutObjectId(string sourceId)
        {
            return "layout-object-" + LayoutBoundaryProbeExecutor.CanonicalSha256(
                new JValue(sourceId ?? "")).Substring(0, 24);
        }

        private static void EnsureIdentityStable(params JObject[] snapshots)
        {
            string[] first = ((JArray)snapshots[0]["objects"]).OfType<JObject>()
                .Select(row => row.Value<string>("id"))
                .OrderBy(value => value, StringComparer.Ordinal).ToArray();
            if (first.Length == 0 || first.Distinct(StringComparer.Ordinal).Count() !=
                    first.Length)
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_OBJECT_IDENTITY_INVALID");
            foreach (JObject snapshot in snapshots.Skip(1))
            {
                string[] current = ((JArray)snapshot["objects"]).OfType<JObject>()
                    .Select(row => row.Value<string>("id"))
                    .OrderBy(value => value, StringComparer.Ordinal).ToArray();
                if (!first.SequenceEqual(current, StringComparer.Ordinal))
                    throw new InvalidOperationException(
                        "LAYOUT_HANDOFF_OBJECT_IDENTITY_DRIFT");
            }
        }

        private static JArray BuildLedger(LayoutPlanningHandoffRequest request)
        {
            return new JArray(
                Ledger("dimension_plan", request.DimensionPlan),
                Ledger("dimensioned_drawing", request.DimensionedDrawing),
                Ledger("dimension_verification_sidecar",
                    request.DimensionVerificationSidecar),
                Ledger("boundary_capability_manifest", request.CapabilityManifest),
                Ledger("boundary_qualification", request.BoundaryQualification));
        }

        private static JObject Ledger(string role, LayoutPlanningArtifact artifact)
        { return new JObject { ["role"] = role, ["path"] = artifact.Path,
            ["sha256_before"] = artifact.Sha256,
            ["sha256_after"] = JValue.CreateNull() }; }

        private static void CompleteLedger(JArray ledger)
        {
            foreach (JObject row in ledger.OfType<JObject>())
                row["sha256_after"] = DimensionPlanningHandoffContract.FileSha256(
                    row.Value<string>("path"));
        }

        private static void EnsureLedgerUnchanged(JArray ledger)
        {
            if (ledger.OfType<JObject>().Any(row =>
                    row.Value<string>("sha256_before") !=
                    row.Value<string>("sha256_after")))
                throw new InvalidOperationException(
                    "LAYOUT_HANDOFF_UPSTREAM_ARTIFACT_CHANGED");
        }

        private static void AtomicWriteJson(string path, JToken value)
        {
            string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
            File.WriteAllText(temporary, value.ToString(Formatting.Indented) +
                System.Environment.NewLine, new System.Text.UTF8Encoding(false));
            File.Move(temporary, path);
        }
    }
}
