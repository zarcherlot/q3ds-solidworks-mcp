using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>G4 native bounded apply/rebuild/readback executor.</summary>
    internal sealed class DrawingLayoutPlanNativeExecutor
    {
        private const int MaximumCycles = 3;
        private const double PositionTolerance = 0.00001;
        private const double LeaderNormalizationTolerance = 0.00025;

        public JObject CaptureDimensionSemantics(IDrawingDoc drawing)
        {
            var rows = new JArray();
            IView view = drawing.GetFirstView() as IView;
            int viewIndex = 0;
            while (view != null)
            {
                if (viewIndex > 0)
                {
                    IDisplayDimension display = view.GetFirstDisplayDimension5();
                    int index = 0;
                    while (display != null)
                    {
                        IDisplayDimension next = display.GetNext5();
                        IAnnotation annotation = null; IDimension dimension = null;
                        try { annotation = display.GetAnnotation() as IAnnotation;
                            dimension = display.GetDimension2(0) as IDimension; } catch { }
                        string name = SafeString(() => display.GetNameForSelection());
                        double value = Double.NaN;
                        try { if (dimension != null) value = Convert.ToDouble(
                            dimension.GetSystemValue3((int)swInConfigurationOpts_e
                                .swThisConfiguration, null), CultureInfo.InvariantCulture); }
                        catch { try { if (dimension != null) value = dimension.SystemValue; } catch { } }
                        rows.Add(new JObject
                        {
                            ["view"] = view.Name, ["selection_name"] = name,
                            ["ordinal"] = index, ["value_si"] = IsFinite(value)
                                ? (JToken)Math.Round(value, 12) : JValue.CreateNull(),
                            ["attached_entity_count"] = annotation != null
                                ? SafeInt(() => annotation.GetAttachedEntityCount3(), -1) : -1
                        });
                        display = next; index++;
                    }
                }
                view = view.GetNextView() as IView; viewIndex++;
            }
            JArray ordered = new JArray(rows.OfType<JObject>().OrderBy(row =>
                row.Value<string>("view"), StringComparer.Ordinal).ThenBy(row =>
                row.Value<string>("selection_name"), StringComparer.Ordinal).ThenBy(row =>
                row.Value<int>("ordinal")));
            return new JObject { ["count"] = ordered.Count, ["dimensions"] = ordered,
                ["fingerprint_sha256"] = LayoutBoundaryProbeExecutor.CanonicalSha256(ordered) };
        }

        public bool TryApply(IModelDoc2 model, IDrawingDoc drawing,
            DrawingLayoutExecutionPlan plan, JObject baselineSemantics,
            JArray baselineViewSemantics,
            out DrawingLayoutNativeResult result, out DrawingLayoutNativeError error)
        {
            result = null; error = null;
            var cycles = new JArray();
            DrawingLayoutNativeError lastError = null;
            try
            {
                for (int cycle = 0; cycle < MaximumCycles; cycle++)
                {
                    foreach (DrawingLayoutExecutionOperation operation in plan.Operations)
                        if (!Apply(drawing, plan, operation, out error)) return false;
                    bool rebuilt = model.ForceRebuild3(false);
                    model.GraphicsRedraw2();
                    JObject snapshot = LayoutBoundaryProbeExecutor.CaptureSnapshot(model,
                        drawing, "g4_cycle_" + cycle);
                    JObject check;
                    if (TryVerifyReadback(drawing, plan, baselineSemantics,
                        baselineViewSemantics, snapshot,
                        out check, out error))
                    {
                        cycles.Add(new JObject { ["cycle"] = cycle + 1,
                            ["rebuild"] = rebuilt, ["verified"] = true,
                            ["layout_fingerprint_sha256"] = check[
                                "layout_fingerprint_sha256"].DeepClone() });
                        result = new DrawingLayoutNativeResult { Verification = check,
                            Cycles = cycles, FinalSnapshot = snapshot };
                        return true;
                    }
                    cycles.Add(new JObject { ["cycle"] = cycle + 1,
                        ["rebuild"] = rebuilt, ["verified"] = false,
                        ["error_code"] = error.Code, ["message"] = error.Message });
                    lastError = error;
                    if (cycle + 1 == MaximumCycles) break;
                    // Finite adjustment is a deterministic re-application of the frozen target;
                    // no unplanned coordinates or semantic mutations are synthesized here.
                }
                error = new DrawingLayoutNativeError { Code = "DRAWING_LAYOUT_ADJUSTMENT_LIMIT",
                    JsonPointer = lastError != null ? lastError.JsonPointer : "",
                    Message = "Layout did not converge after three complete " +
                        "apply/rebuild/readback/collision cycles. Last readback failure: " +
                        (lastError != null
                            ? lastError.Code + ": " + lastError.Message
                            : "unknown") };
                return false;
            }
            catch (Exception ex)
            { return Fail("DRAWING_LAYOUT_NATIVE_EXECUTION_FAILED", "", ex.Message, out error); }
        }

        public bool TryVerifyPersisted(IModelDoc2 model, IDrawingDoc drawing,
            DrawingLayoutExecutionPlan plan, JObject baselineSemantics,
            JArray baselineViewSemantics,
            string expectedFingerprint, out JObject verification,
            out DrawingLayoutNativeError error)
        {
            verification = null; error = null;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                JObject snapshot = LayoutBoundaryProbeExecutor.CaptureSnapshot(model, drawing,
                    "g4_readonly_reopen");
                if (TryVerifyReadback(drawing, plan, baselineSemantics,
                    baselineViewSemantics, snapshot,
                    out verification, out error))
                {
                    if (verification.Value<string>("layout_fingerprint_sha256") !=
                        expectedFingerprint)
                        return Fail("DRAWING_LAYOUT_REOPEN_FINGERPRINT_MISMATCH", "",
                            "The normalized layout fingerprint changed across save/reopen. " +
                            "expected=" + expectedFingerprint + ", actual=" +
                            verification.Value<string>("layout_fingerprint_sha256") + ".",
                            out error);
                    return true;
                }
                try { model.ForceRebuild3(false); } catch { }
            }
            return false;
        }

        public bool TryVerifyCurrent(IModelDoc2 model, IDrawingDoc drawing,
            DrawingLayoutExecutionPlan plan, JObject baselineSemantics,
            JArray baselineViewSemantics, string phase, out JObject verification,
            out DrawingLayoutNativeError error)
        {
            JObject snapshot = LayoutBoundaryProbeExecutor.CaptureSnapshot(
                model, drawing, phase);
            return TryVerifyReadback(drawing, plan, baselineSemantics,
                baselineViewSemantics, snapshot, out verification, out error);
        }

        private static bool Apply(IDrawingDoc drawing, DrawingLayoutExecutionPlan plan,
            DrawingLayoutExecutionOperation operation, out DrawingLayoutNativeError error)
        {
            error = null;
            if (operation.Kind == "set_dimension_hierarchy") return true;
            if (operation.Kind == "move_dimension")
            {
                IAnnotation annotation = FindDimensionAnnotation(drawing,
                    SourceId(plan, operation.ObjectId));
                if (annotation == null || !annotation.SetPosition2(operation.Target[0],
                    operation.Target[1], 0))
                    return Fail("DRAWING_LAYOUT_DIMENSION_MOVE_FAILED", operation.OperationId,
                        "SolidWorks rejected the frozen dimension target.", out error);
                return true;
            }
            if (operation.Kind == "move_annotation")
            {
                IAnnotation annotation = FindNoteAnnotation(drawing,
                    SourceId(plan, operation.ObjectId));
                if (annotation == null || !annotation.SetPosition2(operation.Target[0],
                    operation.Target[1], 0))
                    return Fail("DRAWING_LAYOUT_ANNOTATION_MOVE_FAILED", operation.OperationId,
                        "SolidWorks rejected the frozen annotation target.", out error);
                return true;
            }
            if (operation.Kind == "route_leader")
            {
                IAnnotation annotation = FindOwnerAnnotation(drawing, plan,
                    operation.ObjectId);
                // Plan points are attachment -> annotation.  SolidWorks exposes the same
                // leader in the opposite readback order, while this setter targets the
                // attached-entity endpoint.
                double[] point = operation.Points.First();
                if (annotation == null || annotation.GetLeaderCount() != 1 ||
                    !annotation.SetLeaderAttachmentPointAtIndex(0, point[0], point[1], 0))
                    return Fail("DRAWING_LAYOUT_LEADER_ROUTE_FAILED", operation.OperationId,
                        "The native single-leader route could not be applied exactly.", out error);
                return true;
            }
            if (operation.Kind == "move_view")
            {
                IView view = FindView(drawing, operation.ViewName);
                if (view == null)
                    return Fail("DRAWING_LAYOUT_VIEW_MOVE_FAILED", operation.OperationId,
                        "The authorized view is absent.", out error);
                bool restoreLock = view.PositionLocked;
                if (restoreLock) view.PositionLocked = false;
                view.Position = new[] { operation.Target[0], operation.Target[1] };
                if (restoreLock) view.PositionLocked = true;
                return true;
            }
            if (operation.Kind == "set_view_scale")
            {
                IView view = FindView(drawing, operation.ViewName);
                if (view == null) return Fail("DRAWING_LAYOUT_VIEW_SCALE_FAILED",
                    operation.OperationId, "The authorized view was not found.", out error);
                view.UseParentScale = false; view.UseSheetScale = 0;
                view.ScaleRatio = new double[] { operation.Numerator.Value,
                    operation.Denominator.Value };
                return true;
            }
            ISheet sheet = drawing.GetCurrentSheet() as ISheet;
            if (sheet == null) return Fail("DRAWING_LAYOUT_SHEET_UNAVAILABLE",
                operation.OperationId, "The current sheet was not found.", out error);
            if (operation.Kind == "set_sheet_scale" && !sheet.SetScale(
                operation.Numerator.Value, operation.Denominator.Value, true, true))
                return Fail("DRAWING_LAYOUT_SHEET_SCALE_FAILED", operation.OperationId,
                    "SolidWorks rejected the authorized sheet scale.", out error);
            if (operation.Kind == "set_sheet_format" && !sheet.SetSize(
                (int)swDwgPaperSizes_e.swDwgPapersUserDefined,
                operation.Width.Value, operation.Height.Value))
                return Fail("DRAWING_LAYOUT_SHEET_FORMAT_FAILED", operation.OperationId,
                    "SolidWorks rejected the exact authorized custom sheet size.", out error);
            return true;
        }

        private bool TryVerifyReadback(IDrawingDoc drawing, DrawingLayoutExecutionPlan plan,
            JObject baseline, JArray baselineViewSemantics, JObject snapshot,
            out JObject verification,
            out DrawingLayoutNativeError error)
        {
            verification = null; error = null;
            JObject semantics = CaptureDimensionSemantics(drawing);
            if (!JToken.DeepEquals(baseline, semantics))
                return Fail("DRAWING_LAYOUT_DIMENSION_SEMANTICS_CHANGED", "",
                    "Dimension count, values, identities or attachment counts changed.", out error);
            JArray currentViewSemantics = CaptureViewSemantics(snapshot);
            if (!JToken.DeepEquals(baselineViewSemantics, currentViewSemantics))
                return Fail("DRAWING_LAYOUT_VIEW_SEMANTICS_CHANGED", "",
                    "View type, configuration, display state, parentage or section definition changed.",
                    out error);
            foreach (DrawingLayoutExecutionOperation operation in plan.Operations)
            {
                if (operation.Kind == "move_dimension" || operation.Kind == "move_annotation")
                {
                    IAnnotation annotation = operation.Kind == "move_dimension"
                        ? FindDimensionAnnotation(drawing, SourceId(plan, operation.ObjectId))
                        : FindNoteAnnotation(drawing, SourceId(plan, operation.ObjectId));
                    double[] position = ToDoubleArray(annotation != null ? annotation.GetPosition() : null);
                    if (position.Length < 2 || !Close(position[0], operation.Target[0]) ||
                        !Close(position[1], operation.Target[1]))
                        return Fail("DRAWING_LAYOUT_POSITION_READBACK_MISMATCH",
                            operation.OperationId, "Object position differs from the frozen target.", out error);
                }
                else if (operation.Kind == "move_view")
                {
                    IView view = FindView(drawing, operation.ViewName);
                    double[] position = ToDoubleArray(view != null ? view.Position : null);
                    if (position.Length < 2 || !Close(position[0], operation.Target[0]) ||
                        !Close(position[1], operation.Target[1]))
                        return Fail("DRAWING_LAYOUT_VIEW_READBACK_MISMATCH", operation.OperationId,
                            "View position differs from the frozen target.", out error);
                }
                else if (operation.Kind == "set_view_scale")
                {
                    IView view = FindView(drawing, operation.ViewName);
                    double[] scale = ToDoubleArray(view != null ? view.ScaleRatio : null);
                    if (view == null || view.UseSheetScale != 0 || scale.Length < 2 ||
                        !Close(scale[0], operation.Numerator.Value) ||
                        !Close(scale[1], operation.Denominator.Value))
                        return Fail("DRAWING_LAYOUT_SCALE_READBACK_MISMATCH", operation.OperationId,
                            "View scale differs from the frozen target.", out error);
                }
                else if (operation.Kind == "route_leader")
                {
                    IAnnotation annotation = FindOwnerAnnotation(drawing, plan,
                        operation.ObjectId);
                    double[] native = ToDoubleArray(annotation != null
                        ? annotation.GetLeaderPointsAtIndex(0) : null);
                    IReadOnlyList<double[]> actual = NativeLeaderPoints(native);
                    bool matches = actual.Count == operation.Points.Count &&
                        actual.Zip(operation.Points, (left, right) =>
                            left.Length >= 2 && right.Length >= 2 &&
                            Math.Abs(left[0] - right[0]) <= LeaderNormalizationTolerance &&
                            Math.Abs(left[1] - right[1]) <= LeaderNormalizationTolerance)
                            .All(value => value);
                    if (matches && actual.Count > 0)
                        matches = Close(actual[0][0], operation.Points[0][0]) &&
                            Close(actual[0][1], operation.Points[0][1]);
                    if (!matches)
                        return Fail("DRAWING_LAYOUT_LEADER_READBACK_MISMATCH",
                            operation.OperationId,
                            "Native leader vertices differ from the frozen route. expected=" +
                            FormatPoints(operation.Points) + ", actual=" +
                            FormatNativePoints(native) + ".", out error);
                }
                else if (operation.Kind == "set_sheet_scale")
                {
                    double numerator = snapshot.SelectToken("sheet.scale_numerator")
                        ?.Value<double>() ?? Double.NaN;
                    double denominator = snapshot.SelectToken("sheet.scale_denominator")
                        ?.Value<double>() ?? Double.NaN;
                    if (!Close(numerator, operation.Numerator.Value) ||
                        !Close(denominator, operation.Denominator.Value))
                        return Fail("DRAWING_LAYOUT_SCALE_READBACK_MISMATCH",
                            operation.OperationId,
                            "Sheet scale differs from the frozen target.", out error);
                }
                else if (operation.Kind == "set_sheet_format")
                {
                    double width = snapshot.SelectToken("sheet.width_m")
                        ?.Value<double>() ?? Double.NaN;
                    double height = snapshot.SelectToken("sheet.height_m")
                        ?.Value<double>() ?? Double.NaN;
                    if (!Close(width, operation.Width.Value) ||
                        !Close(height, operation.Height.Value))
                        return Fail("DRAWING_LAYOUT_SHEET_FORMAT_READBACK_MISMATCH",
                            operation.OperationId,
                            "Sheet size differs from the exact authorization.", out error);
                }
            }
            if (!VerifyProjection(plan, snapshot, out error)) return false;
            if (!VerifyGeometry(plan, snapshot, out error)) return false;
            JObject normalized = NormalizeSnapshot(snapshot, plan);
            verification = new JObject { ["verified"] = true,
                ["dimension_semantics"] = semantics,
                ["view_semantics"] = currentViewSemantics,
                ["layout_fingerprint_sha256"] =
                    LayoutBoundaryProbeExecutor.CanonicalSha256(normalized),
                ["snapshot"] = normalized };
            return true;
        }

        private static string FormatPoints(IReadOnlyList<double[]> points)
        {
            return "[" + string.Join(",", points.Select(point => "[" +
                string.Join(",", point.Select(value => value.ToString("R",
                    CultureInfo.InvariantCulture))) + "]")) + "]";
        }

        private static string FormatNativePoints(double[] points)
        {
            var rows = new List<string>();
            for (int index = 0; index + 2 < points.Length; index += 3)
                rows.Add("[" + points[index].ToString("R", CultureInfo.InvariantCulture) +
                    "," + points[index + 1].ToString("R", CultureInfo.InvariantCulture) + "]");
            return "[" + string.Join(",", rows) + "]";
        }

        private static IReadOnlyList<double[]> NativeLeaderPoints(double[] points)
        {
            var rows = new List<double[]>();
            for (int index = 0; index + 2 < points.Length; index += 3)
                rows.Add(new[] { points[index], points[index + 1] });
            rows.Reverse();
            return rows;
        }

        internal static JArray CaptureViewSemantics(JObject snapshot)
        {
            return new JArray(((JArray)snapshot["views"]).OfType<JObject>()
                .OrderBy(row => row.Value<string>("name"), StringComparer.Ordinal)
                .Select(row => new JObject
                {
                    ["name"] = row["name"].DeepClone(),
                    ["view_type"] = row["view_type"].DeepClone(),
                    ["referenced_configuration"] = row["referenced_configuration"].DeepClone(),
                    ["display_state"] = row["display_state"].DeepClone(),
                    ["base_view"] = row["base_view"].DeepClone(),
                    ["section_definition_sha256"] = row["section_definition_sha256"].DeepClone()
                }));
        }

        private static bool VerifyGeometry(DrawingLayoutExecutionPlan plan, JObject snapshot,
            out DrawingLayoutNativeError error)
        {
            error = null;
            JObject handoff = plan.HandoffValue;
            double[] safe = handoff["sheet"]["safe_bounds_m"].Values<double>().ToArray();
            var sourceToId = ((JArray)handoff["objects"]).OfType<JObject>().ToDictionary(
                row => row.Value<string>("source_id"), row => row.Value<string>("id"),
                StringComparer.Ordinal);
            var usable = new HashSet<string>(((JArray)handoff["objects"]).OfType<JObject>()
                .Where(row => row.Value<bool>("collision_usable"))
                .Select(row => row.Value<string>("id")), StringComparer.Ordinal);
            var baseline = ((JArray)handoff["objects"]).OfType<JObject>()
                .Where(row => row.Value<bool>("collision_usable"))
                .ToDictionary(row => row.Value<string>("id"), StringComparer.Ordinal);
            var rows = new List<JObject>();
            foreach (JObject raw in ((JArray)snapshot["objects"]).OfType<JObject>())
            {
                string id;
                if (!sourceToId.TryGetValue(raw.Value<string>("id"), out id) || !usable.Contains(id))
                    continue;
                JObject copy = (JObject)raw.DeepClone(); copy["id"] = id; rows.Add(copy);
                double[] bounds = copy["bounds"].Values<double>().ToArray();
                if (copy.Value<string>("category") != "sheet_border_bounds" &&
                    (bounds[0] < safe[0] - PositionTolerance || bounds[1] < safe[1] - PositionTolerance ||
                     bounds[2] > safe[2] + PositionTolerance || bounds[3] > safe[3] + PositionTolerance))
                {
                    double[] original = baseline[id]["bounds"].Values<double>().ToArray();
                    bool alreadyOutside = original[0] < safe[0] - PositionTolerance ||
                        original[1] < safe[1] - PositionTolerance ||
                        original[2] > safe[2] + PositionTolerance ||
                        original[3] > safe[3] + PositionTolerance;
                    if (!alreadyOutside)
                        return Fail("DRAWING_LAYOUT_SAFE_BOUNDS_VIOLATION", id,
                            "A collision-usable object newly left the frozen safe sheet.", out error);
                }
            }
            var zones = ((JArray)handoff.SelectToken("constraints.locked_zones"))
                .OfType<JObject>().Where(row => row.Value<string>("kind") == "title_block").ToArray();
            foreach (JObject row in rows.Where(item => item.Value<string>("category") !=
                "sheet_border_bounds" && item.Value<string>("category") != "title_block_bounds"))
                foreach (JObject zone in zones)
                    if (PositiveOverlap(row["bounds"].Values<double>().ToArray(),
                        zone["bounds_m"].Values<double>().ToArray()))
                        return Fail("DRAWING_LAYOUT_RESERVED_ZONE_COLLISION",
                            row.Value<string>("id"), "An object intersects the title block.", out error);
            for (int left = 0; left < rows.Count; left++)
                for (int right = left + 1; right < rows.Count; right++)
                {
                    JObject a = rows[left], b = rows[right];
                    string ac = a.Value<string>("category"), bc = b.Value<string>("category");
                    if (ac == "sheet_border_bounds" || bc == "sheet_border_bounds" ||
                        ac == "title_block_bounds" || bc == "title_block_bounds") continue;
                    if (a.Value<string>("view") == b.Value<string>("view") &&
                        (ac == "view_outline_bounds" || bc == "view_outline_bounds")) continue;
                    if (PositiveOverlap(a["bounds"].Values<double>().ToArray(),
                        b["bounds"].Values<double>().ToArray()))
                    {
                        string aid = a.Value<string>("id");
                        string bid = b.Value<string>("id");
                        bool baselineOverlap = PositiveOverlap(
                            baseline[aid]["bounds"].Values<double>().ToArray(),
                            baseline[bid]["bounds"].Values<double>().ToArray());
                        if (!baselineOverlap)
                            return Fail("DRAWING_LAYOUT_POSITIVE_AREA_COLLISION", "",
                                "Objects newly overlap: " + aid + " and " + bid + ".", out error);
                    }
                }
            return true;
        }

        private static bool VerifyProjection(DrawingLayoutExecutionPlan plan, JObject snapshot,
            out DrawingLayoutNativeError error)
        {
            error = null;
            var views = ((JArray)snapshot["views"]).OfType<JObject>().ToDictionary(
                row => row.Value<string>("name"), StringComparer.Ordinal);
            JArray parentage = plan.HandoffValue.SelectToken(
                "constraints.view_parentage") as JArray ?? new JArray();
            foreach (JObject expected in parentage.OfType<JObject>())
            {
                JObject actual; string name = expected.Value<string>("view");
                if (!views.TryGetValue(name, out actual) ||
                    actual.Value<string>("base_view") != expected.Value<string>("parent_view"))
                    return Fail("DRAWING_LAYOUT_PROJECTION_CHANGED", name,
                        "Projected-view parentage changed.", out error);
            }
            JArray alignments = plan.HandoffValue.SelectToken(
                "constraints.projection_alignments") as JArray ?? new JArray();
            foreach (JObject expected in alignments.OfType<JObject>())
            {
                JObject actual; string name = expected.Value<string>("view");
                if (!views.TryGetValue(name, out actual) ||
                    actual.Value<string>("base_view") != expected.Value<string>("parent_view") ||
                    actual.Value<string>("projection_alignment") != expected.Value<string>("axis"))
                    return Fail("DRAWING_LAYOUT_PROJECTION_CHANGED", name,
                        "Projected-view axis alignment changed.", out error);
            }
            return true;
        }

        private static JObject NormalizeSnapshot(JObject snapshot,
            DrawingLayoutExecutionPlan plan)
        {
            var objects = ((JArray)snapshot["objects"]).OfType<JObject>()
                .Select(row => (JObject)row.DeepClone()).ToList();
            var routeBySource = plan.Operations.Where(operation =>
                    operation.Kind == "route_leader")
                .ToDictionary(operation => SourceId(plan, operation.ObjectId),
                    StringComparer.Ordinal);
            var annotationBySource = plan.Operations.Where(operation =>
                    operation.Kind == "move_annotation")
                .ToDictionary(operation => SourceId(plan, operation.ObjectId),
                    StringComparer.Ordinal);
            var handoffBySource = ((JArray)plan.HandoffValue["objects"])
                .OfType<JObject>().ToDictionary(row => row.Value<string>("source_id"),
                    StringComparer.Ordinal);
            foreach (JObject row in objects.Where(row =>
                row.Value<string>("category") == "leader_bounds"))
            {
                DrawingLayoutExecutionOperation operation;
                if (!routeBySource.TryGetValue(row.Value<string>("id"), out operation))
                    continue;
                // The exact attached endpoint is checked before normalization.  SolidWorks
                // canonicalizes the annotation-side and elbow vertices during save/reopen;
                // collapse only that already-bounded native variation onto the frozen route
                // so the persisted fingerprint remains stable.
                row["leader_points_sheet_m"] = new JArray(operation.Points
                    .AsEnumerable().Reverse().Select(point => new JArray(point)));
                row["bounds"] = new JArray(Bounds(operation.Points));
            }
            foreach (JObject row in objects.Where(row =>
                row.Value<string>("category") == "note_text_bounds"))
            {
                DrawingLayoutExecutionOperation operation; JObject frozen;
                string source = row.Value<string>("id");
                if (!annotationBySource.TryGetValue(source, out operation) ||
                    !handoffBySource.TryGetValue(source, out frozen))
                    continue;
                double[] original = frozen["bounds"].Values<double>().ToArray();
                double[] current = frozen["current_position_sheet_m"]
                    .Values<double>().ToArray();
                double dx = operation.Target[0] - current[0];
                double dy = operation.Target[1] - current[1];
                row["bounds"] = new JArray(original[0] + dx, original[1] + dy,
                    original[2] + dx, original[3] + dy);
                row["current_position_sheet_m"] = new JArray(operation.Target);
            }
            return new JObject
            {
                ["sheet"] = snapshot["sheet"] != null ? snapshot["sheet"].DeepClone() : null,
                ["views"] = new JArray(((JArray)snapshot["views"]).OfType<JObject>()
                    .OrderBy(row => row.Value<string>("name"), StringComparer.Ordinal)),
                ["objects"] = new JArray(objects
                    .OrderBy(row => row.Value<string>("id"), StringComparer.Ordinal))
            };
        }
        private static string SourceId(DrawingLayoutExecutionPlan plan, string objectId)
        {
            JObject row = ((JArray)plan.HandoffValue["objects"]).OfType<JObject>()
                .SingleOrDefault(item => item.Value<string>("id") == objectId);
            return row != null ? row.Value<string>("source_id") : null;
        }
        private static IAnnotation FindOwnerAnnotation(IDrawingDoc drawing,
            DrawingLayoutExecutionPlan plan, string leaderObjectId)
        {
            string source = SourceId(plan, leaderObjectId);
            if (source == null || !source.StartsWith("leader:", StringComparison.Ordinal)) return null;
            string owner = source.Substring("leader:".Length);
            int last = owner.LastIndexOf(':'); if (last > 0) owner = owner.Substring(0, last);
            return owner.StartsWith("dimension:", StringComparison.Ordinal)
                ? FindDimensionAnnotation(drawing, owner) : FindNoteAnnotation(drawing, owner);
        }
        private static IAnnotation FindDimensionAnnotation(IDrawingDoc drawing, string sourceId)
        {
            if (String.IsNullOrEmpty(sourceId)) return null;
            IView view = drawing.GetFirstView() as IView; int viewIndex = 0;
            while (view != null)
            {
                if (viewIndex > 0)
                {
                    IDisplayDimension display = view.GetFirstDisplayDimension5(); int index = 0;
                    while (display != null)
                    {
                        string name = SafeString(() => display.GetNameForSelection());
                        string id = "dimension:" + view.Name + ":" +
                            (String.IsNullOrWhiteSpace(name) ? index.ToString() : name);
                        if (id == sourceId) return display.GetAnnotation() as IAnnotation;
                        display = display.GetNext5(); index++;
                    }
                }
                view = view.GetNextView() as IView; viewIndex++;
            }
            return null;
        }
        private static IAnnotation FindNoteAnnotation(IDrawingDoc drawing, string sourceId)
        {
            if (String.IsNullOrEmpty(sourceId)) return null;
            IView view = drawing.GetFirstView() as IView; int viewIndex = 0;
            while (view != null)
            {
                if (viewIndex > 0)
                {
                    Array annotations = view.GetAnnotations() as Array; int index = 0;
                    if (annotations != null) foreach (object value in annotations)
                    {
                        IAnnotation annotation = value as IAnnotation;
                        object specific = null; try { specific = annotation != null
                            ? annotation.GetSpecificAnnotation() : null; } catch { }
                        if (specific is INote && "note:" + view.Name + ":" + index == sourceId)
                            return annotation;
                        index++;
                    }
                }
                view = view.GetNextView() as IView; viewIndex++;
            }
            return null;
        }
        private static IView FindView(IDrawingDoc drawing, string name)
        { IView view = drawing.GetFirstView() as IView; while (view != null)
            { if (view.Name == name) return view; view = view.GetNextView() as IView; }
            return null; }
        private static bool PositiveOverlap(double[] a, double[] b) =>
            Math.Min(a[2], b[2]) - Math.Max(a[0], b[0]) > PositionTolerance &&
            Math.Min(a[3], b[3]) - Math.Max(a[1], b[1]) > PositionTolerance;
        private static bool Close(double a, double b) => Math.Abs(a - b) <= PositionTolerance;
        private static double[] Bounds(IEnumerable<double[]> points)
        {
            double[][] values = points.ToArray();
            return new[] { values.Min(row => row[0]), values.Min(row => row[1]),
                values.Max(row => row[0]), values.Max(row => row[1]) };
        }
        private static double[] BoundsFromTriples(double[] values)
        {
            if (values == null || values.Length < 3) return null;
            var points = new List<double[]>();
            for (int index = 0; index + 1 < values.Length; index += 3)
                points.Add(new[] { values[index], values[index + 1] });
            return points.Count > 0 ? Bounds(points) : null;
        }
        private static bool IsFinite(double value) => !Double.IsNaN(value) && !Double.IsInfinity(value);
        private static string SafeString(Func<string> read) { try { return read() ?? ""; } catch { return ""; } }
        private static int SafeInt(Func<int> read, int fallback) { try { return read(); } catch { return fallback; } }
        private static double[] ToDoubleArray(object value)
        { Array array = value as Array; if (array == null) return new double[0];
            var result = new double[array.Length]; for (int i = 0; i < result.Length; i++)
                result[i] = Convert.ToDouble(array.GetValue(i), CultureInfo.InvariantCulture);
            return result; }
        private static bool Fail(string code, string pointer, string message,
            out DrawingLayoutNativeError error)
        { error = new DrawingLayoutNativeError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }

    internal sealed class DrawingLayoutNativeResult
    { public JObject Verification, FinalSnapshot; public JArray Cycles; }
    internal sealed class DrawingLayoutNativeError
    { public string Code, JsonPointer, Message; }
}
