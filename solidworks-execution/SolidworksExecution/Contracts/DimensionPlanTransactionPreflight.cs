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
            if (viewArray == null || modelArray == null)
                return Fail("DIMENSION_HANDOFF_INVALID", "/handoff",
                    "Handoff view or model-dimension inventory is missing.", out error);
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
                        entity.Value<string>("persistent_reference_kind") != "entity")
                        return Fail("DIMENSION_HANDOFF_BINDING_MISMATCH", "/dimensions",
                            "Attachment differs from target-view handoff geometry: " +
                            attachment.AttachmentId, out error);
                }
            }
            return true;
        }

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
