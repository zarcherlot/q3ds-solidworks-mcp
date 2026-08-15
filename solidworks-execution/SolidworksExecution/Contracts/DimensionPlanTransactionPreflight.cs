using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>F4 COM-free immutable-input, handoff-resolution and no-overwrite gate.</summary>
    public sealed class DimensionPlanTransactionPreflight
    {
        public bool TryValidate(DimensionPlanExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, out DimensionPlanTransactionPaths paths,
            out DimensionPlanContractError error)
        {
            paths = null;
            error = null;
            if (plan == null || plan.Plan == null)
                return Fail("DIMENSION_PLAN_INPUT_INVALID", "", "Compiled plan is required.", out error);

            string frozenPlanPath;
            if (!TryAbsoluteFile(planPath, ".json", out frozenPlanPath, out error,
                "/plan_path", "DIMENSION_PLAN_INPUT_INVALID"))
                return false;
            if (!string.Equals(Path.GetFileName(frozenPlanPath), "dimension_plan.json",
                StringComparison.OrdinalIgnoreCase))
                return Fail("DIMENSION_PLAN_INPUT_INVALID", "/plan_path",
                    "plan_path must name the immutable dimension_plan.json publication.", out error);
            string actualPlanHash = DimensionPlanContractValidator.FileSha256(frozenPlanPath);
            if (!HashEquals(actualPlanHash, planSha256))
                return Fail("DIMENSION_PLAN_INPUT_HASH_MISMATCH", "/plan_sha256",
                    "Published DimensionPlan SHA-256 does not match plan_sha256.", out error);

            JObject diskPlan;
            try { diskPlan = LoadObject(frozenPlanPath); }
            catch (Exception ex)
            {
                return Fail("DIMENSION_PLAN_INPUT_INVALID", "/plan_path", ex.Message, out error);
            }
            if (!JToken.DeepEquals(DimensionPlanContractValidator.Canonicalize(diskPlan), plan.Plan))
                return Fail("DIMENSION_PLAN_INPUT_MISMATCH", "/plan",
                    "Structured plan differs from the immutable plan_path publication.", out error);

            var inputPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                { frozenPlanPath };
            foreach (DimensionPlanArtifactBinding artifact in new[] { plan.Handoff,
                plan.SourceModel, plan.SourceDrawing, plan.ViewPlan, plan.VerificationSidecar })
            {
                string fullPath;
                if (!TryAbsoluteFile(artifact.Path, null, out fullPath, out error, "",
                    "DIMENSION_PLAN_INPUT_INVALID"))
                    return false;
                if (!inputPaths.Add(fullPath))
                    return Fail("DIMENSION_PLAN_INPUT_INVALID", "",
                        "Every frozen DimensionPlan artifact must use a distinct path.", out error);
                if (!HashEquals(DimensionPlanContractValidator.FileSha256(fullPath), artifact.Sha256))
                    return Fail("DIMENSION_PLAN_INPUT_HASH_MISMATCH", "",
                        "Frozen artifact SHA-256 mismatch: " + fullPath, out error);
                artifact.Path = fullPath;
            }
            if (!HasExtension(plan.SourceModel.Path, ".SLDPRT") ||
                !HasExtension(plan.SourceDrawing.Path, ".SLDDRW"))
                return Fail("DIMENSION_PLAN_INPUT_INVALID", "",
                    "The source model and drawing must be .SLDPRT and .SLDDRW files.", out error);

            JObject handoff;
            try { handoff = LoadObject(plan.Handoff.Path); }
            catch (Exception ex)
            {
                return Fail("DIMENSION_HANDOFF_INVALID", "/handoff", ex.Message, out error);
            }
            if (handoff.Value<string>("protocol_id") != "solidworks-dimension-planning-handoff" ||
                handoff.Value<string>("schema_version") != "1.0" ||
                handoff.Value<string>("status") != "ready" ||
                handoff.Value<string>("handoff_id") != plan.HandoffId)
                return Fail("DIMENSION_HANDOFF_INVALID", "/handoff",
                    "The handoff protocol, version, ready status or handoff_id is invalid.", out error);
            JObject handoffModel = handoff["source_model"] as JObject;
            JObject context = handoff["drawing_context"] as JObject;
            if (handoffModel == null || context == null ||
                !PathEquals(handoffModel.Value<string>("path"), plan.SourceModel.Path) ||
                !HashEquals(handoffModel.Value<string>("sha256"), plan.SourceModel.Sha256) ||
                handoffModel.Value<string>("configuration") != plan.Configuration ||
                handoffModel.Value<bool?>("save_flag") != false ||
                !PathEquals(context.Value<string>("path"), plan.SourceDrawing.Path))
                return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/handoff",
                    "The handoff model, drawing or configuration differs from DimensionPlan.", out error);
            if (!ValidateLedger(handoff, plan, out error))
                return false;
            if (!ResolveHandoff(plan, handoff, out error))
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
                return Fail("DIMENSION_OUTPUT_PATH_INVALID", "/output_path", ex.Message, out error);
            }
            if (!HasExtension(outputPath, ".SLDDRW") || inputPaths.Contains(outputPath))
                return Fail("DIMENSION_OUTPUT_PATH_INVALID", "/output_path",
                    "output_path must be a new .SLDDRW path distinct from every frozen input.", out error);
            string directory = Path.GetDirectoryName(outputPath);
            if (string.IsNullOrEmpty(directory) || !Directory.Exists(directory))
                return Fail("DIMENSION_OUTPUT_DIRECTORY_NOT_FOUND", "/output_path",
                    "The output directory must already exist.", out error);
            string reportPath = outputPath + ".dimension-verification.json";
            if (File.Exists(outputPath) || File.Exists(reportPath))
                return Fail("DIMENSION_OUTPUT_EXISTS", "/output_path",
                    "Neither the output drawing nor its sidecar may already exist.", out error);
            paths = new DimensionPlanTransactionPaths
                { PlanPath = frozenPlanPath, PlanFileSha256 = actualPlanHash,
                    OutputPath = outputPath, ReportPath = reportPath };
            return true;
        }

        private static bool ValidateLedger(JObject handoff, DimensionPlanExecutionPlan plan,
            out DimensionPlanContractError error)
        {
            error = null;
            JArray ledger = handoff["upstream_artifacts"] as JArray;
            if (ledger == null) return Fail("DIMENSION_HANDOFF_INVALID", "/handoff/upstream_artifacts",
                "Handoff artifact ledger is missing.", out error);
            var expected = new Dictionary<string, DimensionPlanArtifactBinding>(StringComparer.Ordinal)
            {
                ["source_model"] = plan.SourceModel, ["verified_drawing"] = plan.SourceDrawing,
                ["view_plan"] = plan.ViewPlan, ["verification_sidecar"] = plan.VerificationSidecar
            };
            foreach (var pair in expected)
            {
                JObject[] matches = ledger.OfType<JObject>().Where(item =>
                    item.Value<string>("role") == pair.Key).ToArray();
                JObject row = matches.Length == 1 ? matches[0] : null;
                if (row == null || !PathEquals(row.Value<string>("path"), pair.Value.Path) ||
                    !HashEquals(row.Value<string>("sha256_before"), pair.Value.Sha256) ||
                    !HashEquals(row.Value<string>("sha256_after"), pair.Value.Sha256))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH",
                        "/handoff/upstream_artifacts", "Invalid ledger binding for " + pair.Key + ".",
                        out error);
            }
            return true;
        }

        private static bool ResolveHandoff(DimensionPlanExecutionPlan plan, JObject handoff,
            out DimensionPlanContractError error)
        {
            error = null;
            JArray viewArray = handoff["views"] as JArray;
            JArray modelArray = handoff["model_driven_dimensions"] as JArray;
            JArray featureArray = handoff["manufacturing_features"] as JArray;
            JArray approvedArray = handoff["approved_user_inputs"] as JArray;
            JArray measurementArray = handoff["reference_measurements"] as JArray;
            if (viewArray == null || modelArray == null || featureArray == null ||
                approvedArray == null || measurementArray == null)
                return Fail("DIMENSION_HANDOFF_INVALID", "/handoff",
                    "A required handoff source inventory is missing.", out error);
            var views = new Dictionary<string, JObject>(StringComparer.Ordinal);
            foreach (JObject view in viewArray.OfType<JObject>())
            {
                string id = view.Value<string>("view_id");
                if (string.IsNullOrEmpty(id) || views.ContainsKey(id))
                    return Fail("DIMENSION_HANDOFF_INVALID", "/handoff/views",
                        "Handoff view IDs must be present and unique.", out error);
                views.Add(id, view);
            }
            var modelDimensions = new Dictionary<string, JObject>(StringComparer.Ordinal);
            foreach (JObject dimension in modelArray.OfType<JObject>())
            {
                string id = dimension.Value<string>("dimension_id");
                if (string.IsNullOrEmpty(id) || modelDimensions.ContainsKey(id))
                    return Fail("DIMENSION_HANDOFF_INVALID", "/handoff/model_driven_dimensions",
                        "Handoff model dimension IDs must be present and unique.", out error);
                modelDimensions.Add(id, dimension);
            }
            Dictionary<string, JObject> features;
            Dictionary<string, JObject> approved;
            Dictionary<string, JObject> measurements;
            if (!TryIndex(featureArray, "feature_id", "/handoff/manufacturing_features",
                out features, out error) ||
                !TryIndex(approvedArray, "input_id", "/handoff/approved_user_inputs",
                    out approved, out error) ||
                !TryIndex(measurementArray, "measurement_id", "/handoff/reference_measurements",
                    out measurements, out error))
                return false;
            foreach (DimensionPlanExecutionDimension dimension in plan.Dimensions)
            {
                JObject view;
                if (!views.TryGetValue(dimension.TargetViewId, out view))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                        "Unknown target view: " + dimension.TargetViewId, out error);
                dimension.TargetViewName = view.Value<string>("solidworks_name");
                if (dimension.ImportModelDimension)
                {
                    JObject source;
                    if (!modelDimensions.TryGetValue(dimension.SourceIds.Single(), out source))
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Unknown model dimension source for " + dimension.DimensionId + ".",
                            out error);
                    dimension.ModelDimensionFullName = source.Value<string>("full_name");
                    if (Math.Abs(source.Value<double>("value_si") - dimension.NominalSi) >
                        dimension.ValueTolerance)
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Model dimension value differs from the frozen plan.", out error);
                }
                JArray geometryArray = view["projected_geometry"] as JArray;
                if (geometryArray == null)
                    return Fail("DIMENSION_HANDOFF_INVALID", "/handoff/views",
                        "Target view projected geometry is missing.", out error);
                var geometry = new Dictionary<string, JObject>(StringComparer.Ordinal);
                foreach (JObject entity in geometryArray.OfType<JObject>())
                {
                    string id = entity.Value<string>("entity_id");
                    if (string.IsNullOrEmpty(id) || geometry.ContainsKey(id))
                        return Fail("DIMENSION_HANDOFF_INVALID", "/handoff/views",
                            "Projected entity IDs must be present and unique per view.", out error);
                    geometry.Add(id, entity);
                }
                foreach (DimensionPlanExecutionAttachment attachment in dimension.Attachments)
                {
                    JObject entity;
                    if (!geometry.TryGetValue(attachment.EntityId, out entity) ||
                        entity.Value<string>("model_persistent_reference") !=
                            attachment.PersistentReference ||
                        (!dimension.ImportModelDimension &&
                         entity.Value<string>("persistent_reference_kind") != "entity"))
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Attachment differs from target-view handoff geometry: " +
                            attachment.AttachmentId, out error);
                }
                var boundFeatures = new List<JObject>();
                foreach (string featureId in dimension.FeatureIds)
                {
                    JObject feature;
                    if (!features.TryGetValue(featureId, out feature))
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Unknown manufacturing feature: " + featureId, out error);
                    boundFeatures.Add(feature);
                }
                if (!ValidateSource(dimension, approved, measurements, features, out error))
                    return false;
                if (dimension.Kind.StartsWith("hole_", StringComparison.Ordinal) &&
                    !boundFeatures.Any(item => item.Value<string>("classification") == "hole" ||
                        item.Value<string>("classification").EndsWith("pattern",
                            StringComparison.Ordinal)))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                        "Hole/array annotation has no bound hole or pattern feature.", out error);
                if (dimension.Kind == "slot" && !boundFeatures.Any(item =>
                    item.Value<string>("classification") == "slot"))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                        "Slot dimension has no bound slot feature.", out error);
                dimension.FitTarget = boundFeatures.Any(item =>
                    item.Value<string>("classification") == "hole" ||
                    item.Value<string>("classification") == "slot") ? "hole" : "shaft";
                if (dimension.UseOrdinate)
                {
                    JObject first = GeometryForRole(dimension, geometry, "first");
                    JObject second = GeometryForRole(dimension, geometry, "second");
                    double[] firstPoint = GeometryAnchor(first);
                    double[] secondPoint = GeometryAnchor(second);
                    if (firstPoint == null || secondPoint == null)
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Ordinate dimension attachments have no deterministic sheet anchors.",
                            out error);
                    dimension.OrdinateType = Math.Abs(secondPoint[0] - firstPoint[0]) >=
                        Math.Abs(secondPoint[1] - firstPoint[1]) ? 3 : 2;
                }
            }
            return true;
        }

        private static bool ValidateSource(DimensionPlanExecutionDimension dimension,
            IDictionary<string, JObject> approved, IDictionary<string, JObject> measurements,
            IDictionary<string, JObject> features, out DimensionPlanContractError error)
        {
            error = null;
            if (dimension.SourceTier == "user_confirmed_input")
            {
                var rows = new List<JObject>();
                foreach (string id in dimension.SourceIds)
                {
                    JObject row;
                    if (!approved.TryGetValue(id, out row))
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Unknown approved input: " + id, out error);
                    rows.Add(row);
                    JArray targets = row["target_feature_ids"] as JArray;
                    if (targets == null || targets.Values<string>().Any(idValue =>
                        !dimension.FeatureIds.Contains(idValue)))
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Approved input targets features outside the dimension binding.", out error);
                }
                if (!rows.Any(row => ApprovedQuantityMatches(row, dimension.QuantityKind,
                    dimension.NominalSi)))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                        "Frozen nominal is not present in approved inputs.", out error);
                if (dimension.Tolerance != null)
                {
                    if (dimension.Tolerance.Kind == "fit")
                    {
                        if (!rows.Any(row => row["value"] != null &&
                            row["value"].Value<string>("kind") == "exact_text" &&
                            row["value"].Value<string>("text") == dimension.Tolerance.FitCode))
                            return Fail("DIMENSION_TOLERANCE_UNTRUSTED", "/dimensions",
                                "Fit code is absent from exact approved text inputs.", out error);
                    }
                    else if (!rows.Any(row => ApprovedQuantityValue(row,
                        dimension.QuantityKind, dimension.Tolerance.LowerSi.Value)) ||
                        !rows.Any(row => ApprovedQuantityValue(row,
                        dimension.QuantityKind, dimension.Tolerance.UpperSi.Value)))
                        return Fail("DIMENSION_TOLERANCE_UNTRUSTED", "/dimensions",
                            "Tolerance limits are absent from approved quantity inputs.", out error);
                }
            }
            else if (dimension.SourceTier == "reference_geometry_measurement")
            {
                if (!dimension.SourceIds.Any(id => measurements.ContainsKey(id) &&
                    Close(measurements[id].Value<double>("value_si"), dimension.NominalSi) &&
                    measurements[id].Value<string>("view_id") == dimension.TargetViewId))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                        "Reference nominal/view is not present in frozen measurements.", out error);
            }
            else if (dimension.HandoffCollection == "manufacturing_features")
            {
                if (dimension.SourceIds.Any(id => !features.ContainsKey(id)) ||
                    dimension.SourceIds.Any(id => !dimension.FeatureIds.Contains(id)))
                    return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                        "Manufacturing-feature source is not bound to feature_ids.", out error);
            }
            return true;
        }

        private static bool TryIndex(JArray rows, string key, string pointer,
            out Dictionary<string, JObject> index, out DimensionPlanContractError error)
        {
            index = new Dictionary<string, JObject>(StringComparer.Ordinal); error = null;
            foreach (JObject row in rows.OfType<JObject>())
            {
                string id = row.Value<string>(key);
                if (string.IsNullOrEmpty(id) || index.ContainsKey(id))
                    return Fail("DIMENSION_HANDOFF_INVALID", pointer,
                        "Source IDs must be present and unique.", out error);
                index.Add(id, row);
            }
            return true;
        }

        private static bool ApprovedQuantityMatches(JObject row, string kind, double value)
        {
            JObject approved = row["value"] as JObject;
            return approved != null && approved.Value<string>("kind") == "quantity" &&
                approved.Value<string>("quantity_kind") == kind &&
                Close(approved.Value<double>("value_si"), value);
        }

        private static bool ApprovedQuantityValue(JObject row, string kind, double value)
        {
            JObject approved = row["value"] as JObject;
            return approved != null && approved.Value<string>("kind") == "quantity" &&
                approved.Value<string>("quantity_kind") == kind &&
                Close(approved.Value<double>("value_si"), value);
        }

        private static JObject GeometryForRole(DimensionPlanExecutionDimension dimension,
            IDictionary<string, JObject> geometry, string role)
        {
            DimensionPlanExecutionAttachment attachment = dimension.Attachments.FirstOrDefault(
                item => item.Role == role);
            JObject result;
            return attachment != null && geometry.TryGetValue(attachment.EntityId, out result)
                ? result : null;
        }

        private static double[] GeometryAnchor(JObject entity)
        {
            JArray values = entity != null ? entity["geometry_sheet_m"] as JArray : null;
            if (values == null || values.Count < 2) return null;
            double[] raw = values.Values<double>().ToArray();
            if (raw.Length >= 4)
                return new[] { (raw[0] + raw[2]) / 2.0, (raw[1] + raw[3]) / 2.0 };
            return new[] { raw[0], raw[1] };
        }

        private static bool Close(double first, double second) =>
            Math.Abs(first - second) <= 1e-12;

        private static JObject LoadObject(string path)
        {
            using (var stream = File.OpenText(path))
            using (var reader = new JsonTextReader(stream) { DateParseHandling = DateParseHandling.None })
            {
                var value = JObject.Load(reader, new JsonLoadSettings
                    { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                        CommentHandling = CommentHandling.Ignore,
                        LineInfoHandling = LineInfoHandling.Ignore });
                if (reader.Read()) throw new InvalidDataException("JSON contains trailing content.");
                return value;
            }
        }

        private static bool TryAbsoluteFile(string value, string extension, out string fullPath,
            out DimensionPlanContractError error, string pointer, string code)
        {
            fullPath = null; error = null;
            try
            {
                if (string.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value))
                    throw new InvalidDataException("path must be absolute");
                fullPath = Path.GetFullPath(value);
                if (!File.Exists(fullPath)) throw new FileNotFoundException("file not found", fullPath);
                if (extension != null && !HasExtension(fullPath, extension))
                    throw new InvalidDataException("unexpected file extension");
                return true;
            }
            catch (Exception ex) { return Fail(code, pointer, ex.Message, out error); }
        }

        private static bool HashEquals(string first, string second) =>
            !string.IsNullOrWhiteSpace(first) && first.Length == 64 &&
            string.Equals(first, second, StringComparison.OrdinalIgnoreCase);
        private static bool PathEquals(string first, string second)
        {
            try { return string.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                StringComparison.OrdinalIgnoreCase); } catch { return false; }
        }
        private static bool HasExtension(string path, string extension) =>
            string.Equals(Path.GetExtension(path), extension, StringComparison.OrdinalIgnoreCase);
        private static bool Fail(string code, string pointer, string message,
            out DimensionPlanContractError error)
        {
            error = new DimensionPlanContractError
                { Code = code, JsonPointer = pointer, Message = message };
            return false;
        }
    }

    public sealed class DimensionPlanTransactionPaths
    {
        public string PlanPath, PlanFileSha256, OutputPath, ReportPath;
    }
}
