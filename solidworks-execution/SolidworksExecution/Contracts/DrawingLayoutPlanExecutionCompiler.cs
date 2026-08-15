using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>Fail-closed compiler for the complete DrawingLayoutPlan 1.0 operation union.</summary>
    public sealed class DrawingLayoutPlanExecutionCompiler
    {
        private static readonly IDictionary<string, int> Phases =
            new Dictionary<string, int>(StringComparer.Ordinal)
            {
                ["set_dimension_hierarchy"] = 0, ["move_dimension"] = 0,
                ["move_annotation"] = 1, ["route_leader"] = 1,
                ["move_view"] = 2, ["set_view_scale"] = 3,
                ["set_sheet_scale"] = 4, ["set_sheet_format"] = 5
            };

        public bool TryCompile(DrawingLayoutPlanDocument document,
            out DrawingLayoutExecutionPlan plan, out DrawingLayoutPlanContractError error)
        {
            plan = null; error = null;
            if (document == null || document.CanonicalPlan == null)
                return Fail("DRAWING_LAYOUT_PLAN_COMPILE_INVALID", "",
                    "DrawingLayoutPlan is required.", out error);
            JObject root = document.CanonicalPlan;
            var compiled = new DrawingLayoutExecutionPlan
            {
                PlanId = document.PlanId, PlanSha256 = document.CanonicalSha256,
                Plan = (JObject)root.DeepClone(), HandoffId = root.Value<string>("handoff_id"),
                Configuration = root.Value<string>("configuration"),
                Handoff = Artifact(root["handoff"]),
                SourceDimensionPlan = Artifact(root["source_dimension_plan"]),
                SourceDrawing = Artifact(root["source_drawing"]),
                DimensionVerificationSidecar = Artifact(root["dimension_verification_sidecar"]),
                DimensionSemanticSha256 = root["source_invariants"].Value<string>(
                    "dimension_semantics_sha256"),
                ObjectSnapshotSha256 = root["source_invariants"].Value<string>(
                    "object_snapshot_sha256"),
                DimensionIds = Strings(root["source_invariants"]["dimension_ids"]),
                ObjectIds = Strings(root["source_invariants"]["object_ids"]),
                ViewNames = Strings(root["source_invariants"]["view_names"]),
                LockedObjectIds = Strings(root["source_invariants"]["locked_object_ids"]),
                RequiredBoundaryCapabilities = Strings(root["source_invariants"]
                    ["required_boundary_capabilities"]),
                MovableViewNames = Strings(root["authorization"]["movable_view_names"]),
                ScalableViewNames = Strings(root["authorization"]["scalable_view_names"]),
                AllowSheetScaleChange = root["authorization"].Value<bool>(
                    "allow_sheet_scale_change"),
                Operations = new List<DrawingLayoutExecutionOperation>()
            };
            foreach (JObject authorization in ((JArray)root["authorization"]
                ["allowed_sheet_formats"]).OfType<JObject>())
                compiled.AllowedSheetFormats.Add(new DrawingLayoutSheetFormat
                {
                    AuthorizationId = authorization.Value<string>("authorization_id"),
                    FormatId = authorization.Value<string>("format_id"),
                    Width = authorization.Value<double>("width_m"),
                    Height = authorization.Value<double>("height_m")
                });

            int previousPhase = -1;
            JArray operations = (JArray)root["operations"];
            var operationIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < operations.Count; index++)
            {
                JObject item = (JObject)operations[index];
                string pointer = "/operations/" + index;
                string kind = item.Value<string>("kind");
                int phase;
                if (!Phases.TryGetValue(kind, out phase))
                    return Fail("DRAWING_LAYOUT_CAPABILITY_BLOCKED", pointer + "/kind",
                        "Unsupported layout operation: " + kind + ".", out error);
                if (item.Value<int>("sequence") != index)
                    return Fail("DRAWING_LAYOUT_PLAN_SEQUENCE_INVALID", pointer + "/sequence",
                        "Operation sequences must be contiguous and zero-based.", out error);
                if (phase < previousPhase)
                    return Fail("DRAWING_LAYOUT_PLAN_PHASE_INVALID", pointer + "/kind",
                        "Layout operations violate the frozen six-stage order.", out error);
                previousPhase = phase;
                if (!operationIds.Add(item.Value<string>("operation_id")))
                    return Fail("DRAWING_LAYOUT_PLAN_COMPILE_INVALID", pointer + "/operation_id",
                        "Operation IDs must be unique.", out error);
                var operation = new DrawingLayoutExecutionOperation
                {
                    OperationId = item.Value<string>("operation_id"), Kind = kind,
                    Sequence = index, Phase = phase, ObjectId = item.Value<string>("object_id"),
                    DimensionId = item.Value<string>("dimension_id"),
                    ViewName = item.Value<string>("view_name"), Tier = item.Value<string>("tier"),
                    StackIndex = item.Value<int?>("stack_index"),
                    Numerator = item.Value<int?>("numerator"),
                    Denominator = item.Value<int?>("denominator"),
                    AuthorizationId = item.Value<string>("authorization_id"),
                    FormatId = item.Value<string>("format_id"),
                    Width = item.Value<double?>("width_m"), Height = item.Value<double?>("height_m")
                };
                JArray target = item["target_position_sheet_m"] as JArray;
                if (target != null) operation.Target = target.Values<double>().ToArray();
                JArray points = item["points_sheet_m"] as JArray;
                if (points != null) operation.Points = points.OfType<JArray>()
                    .Select(row => row.Values<double>().ToArray()).ToList();
                if (!ValidateAuthorization(compiled, operation, pointer, out error)) return false;
                compiled.Operations.Add(operation);
            }
            plan = compiled;
            return true;
        }

        private static bool ValidateAuthorization(DrawingLayoutExecutionPlan plan,
            DrawingLayoutExecutionOperation operation, string pointer,
            out DrawingLayoutPlanContractError error)
        {
            error = null;
            if (operation.ObjectId != null && plan.LockedObjectIds.Contains(operation.ObjectId))
                return Fail("DRAWING_LAYOUT_OPERATION_LOCKED", pointer + "/object_id",
                    "A frozen object cannot be moved.", out error);
            if (operation.Kind == "move_view" &&
                !plan.MovableViewNames.Contains(operation.ViewName))
                return Fail("DRAWING_LAYOUT_OPERATION_UNAUTHORIZED", pointer + "/view_name",
                    "View movement is not authorized.", out error);
            if (operation.Kind == "set_view_scale" &&
                !plan.ScalableViewNames.Contains(operation.ViewName))
                return Fail("DRAWING_LAYOUT_OPERATION_UNAUTHORIZED", pointer + "/view_name",
                    "Local view scale change is not authorized.", out error);
            if (operation.Kind == "set_sheet_scale" && !plan.AllowSheetScaleChange)
                return Fail("DRAWING_LAYOUT_OPERATION_UNAUTHORIZED", pointer,
                    "Sheet scale change is not authorized.", out error);
            if (operation.Kind == "set_sheet_format" && !plan.AllowedSheetFormats.Any(item =>
                item.AuthorizationId == operation.AuthorizationId &&
                item.FormatId == operation.FormatId && item.Width == operation.Width &&
                item.Height == operation.Height))
                return Fail("DRAWING_LAYOUT_OPERATION_UNAUTHORIZED", pointer,
                    "Sheet format differs from the exact approved authorization.", out error);
            return true;
        }

        private static DrawingLayoutArtifactBinding Artifact(JToken token)
        {
            var value = (JObject)token;
            return new DrawingLayoutArtifactBinding { Path = value.Value<string>("path"),
                Sha256 = value.Value<string>("sha256") };
        }
        private static HashSet<string> Strings(JToken token) => new HashSet<string>(
            ((JArray)token).Values<string>(), StringComparer.Ordinal);
        private static bool Fail(string code, string pointer, string message,
            out DrawingLayoutPlanContractError error)
        { error = new DrawingLayoutPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }

    public sealed class DrawingLayoutExecutionPlan
    {
        public string PlanId, PlanSha256, HandoffId, Configuration,
            DimensionSemanticSha256, ObjectSnapshotSha256;
        public JObject Plan;
        public DrawingLayoutArtifactBinding Handoff, SourceDimensionPlan, SourceDrawing,
            DimensionVerificationSidecar;
        public HashSet<string> DimensionIds, ObjectIds, ViewNames, LockedObjectIds,
            RequiredBoundaryCapabilities, MovableViewNames, ScalableViewNames;
        public bool AllowSheetScaleChange;
        public List<DrawingLayoutSheetFormat> AllowedSheetFormats = new List<DrawingLayoutSheetFormat>();
        public List<DrawingLayoutExecutionOperation> Operations;
        public JObject HandoffValue;
        public Dictionary<string, DrawingLayoutArtifactBinding> UpstreamDimensionArtifacts =
            new Dictionary<string, DrawingLayoutArtifactBinding>(StringComparer.Ordinal);
    }
    public sealed class DrawingLayoutArtifactBinding { public string Path, Sha256; }
    public sealed class DrawingLayoutSheetFormat
    { public string AuthorizationId, FormatId; public double Width, Height; }
    public sealed class DrawingLayoutExecutionOperation
    {
        public string OperationId, Kind, ObjectId, DimensionId, ViewName, Tier,
            AuthorizationId, FormatId;
        public int Sequence, Phase; public int? StackIndex, Numerator, Denominator;
        public double? Width, Height; public double[] Target;
        public List<double[]> Points;
    }
}
