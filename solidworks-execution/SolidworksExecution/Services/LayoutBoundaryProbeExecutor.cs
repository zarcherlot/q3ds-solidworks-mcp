using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Read-only G0 native boundary probe. It never saves the drawing and emits
    /// evidence only after a complete close/read-only-reopen comparison.
    /// </summary>
    internal sealed class LayoutBoundaryProbeExecutor
    {
        private const string EvidenceProtocol = "solidworks-layout-boundary-evidence";
        private const string ManagedAuxiliaryLabelPrefix = "Q3DS_AUX_LABEL_";
        private readonly ISldWorks _solidWorks;

        public LayoutBoundaryProbeExecutor(ISldWorks solidWorks)
        {
            _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks");
        }

        public JObject Execute(LayoutBoundaryProbeRequest request, JObject sourceRequest)
        {
            Directory.CreateDirectory(request.PublicationDirectory);
            string beforePath = Path.Combine(request.PublicationDirectory,
                "layout-boundary-before-rebuild.json");
            string rebuiltPath = Path.Combine(request.PublicationDirectory,
                "layout-boundary-after-rebuild.json");
            string reopenedPath = Path.Combine(request.PublicationDirectory,
                "layout-boundary-readonly-reopen.json");
            string evidencePath = Path.Combine(request.PublicationDirectory,
                "layout-boundary-evidence.json");
            string requestPath = Path.Combine(request.PublicationDirectory,
                "layout-boundary-probe-request.json");
            string requestHash = CanonicalSha256(sourceRequest);
            AtomicWriteJson(requestPath, sourceRequest);
            var upstream = BuildUpstreamRows(request);
            string revision = _solidWorks.RevisionNumber();
            IModelDoc2 model = null;
            JObject before = null;
            JObject rebuilt = null;
            JObject reopened = null;
            try
            {
                if (_solidWorks.GetOpenDocumentByName(
                        request.Drawing.Path) != null)
                    throw new InvalidOperationException(
                        "G0 source drawing is already open; read-only ownership is ambiguous.");
                model = OpenReadOnly(request.Drawing.Path);
                var drawing = model as IDrawingDoc;
                if (drawing == null)
                    throw new InvalidOperationException("G0 source is not a drawing document.");
                before = CaptureSnapshot(model, drawing, "before_rebuild");
                AtomicWriteJson(beforePath, before);

                RebuildForReadback(model);
                rebuilt = CaptureSnapshot(model, drawing, "after_rebuild");
                AtomicWriteJson(rebuiltPath, rebuilt);
                string title = model.GetTitle();
                _solidWorks.CloseDoc(title);
                model = null;

                model = OpenReadOnly(request.Drawing.Path);
                drawing = model as IDrawingDoc;
                if (drawing == null)
                    throw new InvalidOperationException("reopened G0 source is not a drawing.");
                RebuildForReadback(model);
                reopened = CaptureSnapshot(model, drawing, "readonly_reopen");
                AtomicWriteJson(reopenedPath, reopened);
                _solidWorks.CloseDoc(model.GetTitle());
                model = null;

                CompleteUpstreamRows(upstream);
                var evidence = new JObject
                {
                    ["protocol_id"] = EvidenceProtocol,
                    ["schema_version"] = "1.0",
                    ["probe_id"] = "LBE-" + requestHash.Substring(0, 16),
                    ["created_at"] = DateTime.UtcNow.ToString("o"),
                    ["execution_mode"] = "live",
                    ["source_kind"] = request.SourceKind,
                    ["solidworks"] = SolidWorksIdentity(revision),
                    ["source_request_sha256"] = requestHash,
                    ["error_budget_m"] = request.ErrorBudgetMeters,
                    ["upstream_immutability"] = upstream,
                    ["snapshots"] = new JObject
                    {
                        ["before_rebuild_sha256"] = CanonicalSha256(before),
                        ["after_rebuild_sha256"] = CanonicalSha256(rebuilt),
                        ["readonly_reopen_sha256"] = CanonicalSha256(reopened)
                    },
                    ["capabilities"] = BuildCapabilityRows(before, rebuilt, reopened,
                        request.ErrorBudgetMeters)
                };
                AtomicWriteJson(evidencePath, evidence);
                return new JObject
                {
                    ["status"] = "pass",
                    ["evidence_path"] = evidencePath,
                    ["evidence_sha256"] = FileSha256(evidencePath),
                    ["source_request_sha256"] = requestHash,
                    ["capabilities"] = evidence["capabilities"].DeepClone()
                };
            }
            finally
            {
                if (model != null)
                {
                    try { _solidWorks.CloseDoc(model.GetTitle()); } catch { }
                }
            }
        }

        internal IModelDoc2 OpenReadOnly(string path)
        {
            int errors = 0;
            int warnings = 0;
            var model = _solidWorks.OpenDoc6(path,
                (int)swDocumentTypes_e.swDocDRAWING,
                (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                "", ref errors, ref warnings) as IModelDoc2;
            if (model == null || errors != 0)
                throw new InvalidOperationException(
                    "read-only drawing open failed (errors=" + errors +
                    ", warnings=" + warnings + ").");
            return model;
        }

        internal static JObject CaptureSnapshot(IModelDoc2 model, IDrawingDoc drawing,
            string phase)
        {
            var objects = new JArray();
            var views = new JArray();
            var sheet = drawing.GetCurrentSheet() as ISheet;
            string sheetName = "Sheet";
            JObject sheetState = new JObject();
            if (sheet != null)
            {
                try { sheetName = sheet.GetName(); } catch { }
                double width = 0.0;
                double height = 0.0;
                sheet.GetSize(ref width, ref height);
                sheetState["name"] = sheetName;
                sheetState["width_m"] = Math.Round(width, 12);
                sheetState["height_m"] = Math.Round(height, 12);
                double[] properties = ToDoubleArray(sheet.GetProperties2());
                if (properties.Length >= 4)
                {
                    sheetState["scale_numerator"] = Math.Round(properties[2], 12);
                    sheetState["scale_denominator"] = Math.Round(properties[3], 12);
                }
                AddBound(objects, "sheet-border", "sheet_border_bounds", "Sheet",
                    new[] { 0.0, 0.0, width, height }, "ISheet.GetSize", true);
                var titleBlock = sheet.TitleBlock as ITitleBlock;
                if (titleBlock != null)
                {
                    double left = 0, top = 0, right = 0, bottom = 0;
                    titleBlock.GetExtents(ref left, ref top, ref right, ref bottom);
                    AddBound(objects, "title-block", "title_block_bounds", "Sheet",
                        NormalizeBounds(new[] { left, bottom, right, top }),
                        "ITitleBlock.GetExtents", true);
                }
            }

            IView view = drawing.GetFirstView() as IView;
            int viewIndex = 0;
            while (view != null)
            {
                IView next = view.GetNextView() as IView;
                string viewName = String.IsNullOrWhiteSpace(view.Name)
                    ? "view-" + viewIndex : view.Name;
                bool sheetView = viewIndex == 0;
                if (!sheetView)
                {
                    double[] position = ToDoubleArray(view.Position);
                    IView baseView = null;
                    try { baseView = view.GetBaseView() as IView; } catch { }
                    string baseName = baseView != null ? SafeString(() => baseView.Name) : "";
                    var viewRow = new JObject
                    {
                        ["name"] = viewName,
                        ["position_sheet_m"] = position.Length >= 2
                            ? new JArray(Math.Round(position[0], 12), Math.Round(position[1], 12))
                            : new JArray(),
                        ["position_locked"] = view.PositionLocked,
                        ["use_sheet_scale"] = view.UseSheetScale,
                        ["use_parent_scale"] = view.UseParentScale,
                        ["view_type"] = view.Type,
                        ["referenced_configuration"] = SafeString(
                            () => view.ReferencedConfiguration),
                        ["display_state"] = SafeString(() => view.DisplayState),
                        ["section_definition_sha256"] = SectionDefinitionSha256(view),
                        ["base_view"] = String.IsNullOrWhiteSpace(baseName)
                            ? JValue.CreateNull() : (JToken)baseName
                    };
                    double[] scale = ToDoubleArray(view.ScaleRatio);
                    if (scale.Length >= 2)
                        viewRow["scale_ratio"] = new JArray(Math.Round(scale[0], 12),
                            Math.Round(scale[1], 12));
                    if (baseView != null && position.Length >= 2)
                    {
                        double[] basePosition = ToDoubleArray(baseView.Position);
                        if (basePosition.Length >= 2)
                            viewRow["projection_alignment"] =
                                Math.Abs(position[0] - basePosition[0]) >=
                                Math.Abs(position[1] - basePosition[1])
                                ? "horizontal" : "vertical";
                    }
                    views.Add(viewRow);
                    double[] outline = ToDoubleArray(view.GetOutline());
                    if (outline.Length >= 4)
                        AddBound(objects, "view:" + viewName, "view_outline_bounds",
                            viewName, NormalizeBounds(outline.Take(4).ToArray()),
                            "IView.GetOutline", true);
                    CaptureDimensions(objects, view, viewName);
                    CaptureAnnotations(objects, view, viewName);
                    CaptureSectionSymbols(objects, view, viewName);
                    CaptureCenterElements(objects, view, viewName);
                }
                view = next;
                viewIndex++;
            }
            return new JObject
            {
                ["phase"] = phase,
                ["drawing_title"] = model.GetTitle(),
                ["sheet_name"] = sheetName,
                ["sheet"] = sheetState,
                ["views"] = views,
                ["objects"] = objects
            };
        }

        private static void CaptureDimensions(JArray objects, IView view, string viewName)
        {
            IDisplayDimension display = view.GetFirstDisplayDimension5();
            int index = 0;
            while (display != null)
            {
                IDisplayDimension next = display.GetNext5();
                IAnnotation annotation = null;
                try { annotation = display.GetAnnotation() as IAnnotation; } catch { }
                if (annotation != null)
                {
                    string name = null;
                    try { name = display.GetNameForSelection(); } catch { }
                    string id = "dimension:" + viewName + ":" +
                        (String.IsNullOrWhiteSpace(name) ? index.ToString() : name);
                    BoundResult bound = DisplayBounds(annotation);
                    if (bound != null)
                    {
                        JObject row = AddBound(objects, id,
                            "dimension_display_bounds", viewName,
                            bound.Bounds, bound.Api, bound.Exact);
                        double[] position = ToDoubleArray(annotation.GetPosition());
                        if (row != null && position.Length >= 2)
                            row["current_position_sheet_m"] = new JArray(
                                Math.Round(position[0], 12),
                                Math.Round(position[1], 12));
                    }
                    CaptureLeaders(objects, annotation, id, viewName);
                }
                display = next;
                index++;
            }
        }

        private static void CaptureAnnotations(JArray objects, IView view, string viewName)
        {
            Array annotations = view.GetAnnotations() as Array;
            if (annotations == null) return;
            int index = 0;
            foreach (object item in annotations)
            {
                var annotation = item as IAnnotation;
                if (annotation == null) { index++; continue; }
                object specific = null;
                try { specific = annotation.GetSpecificAnnotation(); } catch { }
                var note = specific as INote;
                if (note != null)
                {
                    double[] extent = ToDoubleArray(note.GetExtent());
                    string id = "note:" + viewName + ":" + index;
                    string annotationName = SafeString(() => annotation.GetName());
                    string noteName = SafeString(() => note.GetName());
                    string tagName = SafeString(() => note.TagName);
                    string noteText = SafeString(() => note.GetText());
                    int annotationType = SafeInt(() => annotation.GetType(), -1);
                    int ownerType = SafeInt(() => annotation.OwnerType, -1);
                    int leaderCount = SafeInt(() => annotation.GetLeaderCount(), 0);
                    int attachedEntityCount = SafeInt(
                        () => annotation.GetAttachedEntityCount3(), -1);
                    double[] noteBounds = BoundsFromTriples(extent);
                    if (noteBounds != null)
                    {
                        JObject noteRow = AddBound(objects, id, "note_text_bounds",
                            viewName, noteBounds,
                            "INote.GetExtent", true);
                        double[] position = ToDoubleArray(annotation.GetPosition());
                        if (noteRow != null && position.Length >= 2)
                            noteRow["current_position_sheet_m"] = new JArray(
                                Math.Round(position[0], 12),
                                Math.Round(position[1], 12));
                        AddAnnotationMetadata(noteRow, annotationName, noteName,
                            tagName, noteText, annotationType, ownerType, leaderCount,
                            attachedEntityCount);
                        if (IsManagedAuxiliaryLabel(annotationName, annotationType,
                                ownerType, leaderCount, attachedEntityCount))
                        {
                            JObject labelRow = AddBound(objects,
                                "managed-auxiliary-label:" + annotationName,
                                "view_label_bounds", viewName, noteBounds,
                                "INote.GetExtent+managed auxiliary-label identity", true);
                            AddAnnotationMetadata(labelRow, annotationName, noteName,
                                tagName, noteText, annotationType, ownerType,
                                leaderCount, attachedEntityCount);
                            if (labelRow != null)
                                labelRow["label_kind"] = "managed_auxiliary";
                        }
                    }
                    CaptureLeaders(objects, annotation, id, viewName);
                }
                index++;
            }
        }

        private static void CaptureNativeViewLabels(JArray objects, IView view,
            string viewName)
        {
            IDetailCircle detail = null;
            try { detail = view.GetDetail() as IDetailCircle; } catch { }
            if (detail == null) return;
            string label = SafeString(() => detail.GetLabel());
            if (String.IsNullOrWhiteSpace(label)) return;
            double x = 0.0;
            double y = 0.0;
            try { detail.GetLabelPosition(out x, out y); }
            catch { return; }
            if (!IsFinite(x) || !IsFinite(y)) return;
            ITextFormat format = null;
            try { format = detail.GetTextFormat() as ITextFormat; } catch { }
            double[] bounds = ApproximateTextBounds(x, y, label, format);
            JObject row = AddBound(objects, "native-detail-label:" + viewName,
                "view_label_bounds", viewName, bounds,
                "IDetailCircle.GetLabelPosition/GetTextFormat approximation", false);
            if (row != null)
            {
                row["label_kind"] = "native_detail";
                row["text"] = label;
                row["anchor_sheet_m"] = new JArray(Math.Round(x, 12),
                    Math.Round(y, 12));
            }
        }

        private static bool IsManagedAuxiliaryLabel(string annotationName,
            int annotationType, int ownerType, int leaderCount,
            int attachedEntityCount)
        {
            return !String.IsNullOrWhiteSpace(annotationName) &&
                annotationName.StartsWith(ManagedAuxiliaryLabelPrefix,
                    StringComparison.Ordinal) &&
                annotationType == 6 && ownerType == 0 && leaderCount == 0 &&
                attachedEntityCount == 0;
        }

        private static void AddAnnotationMetadata(JObject row,
            string annotationName, string noteName, string tagName, string text,
            int annotationType, int ownerType, int leaderCount,
            int attachedEntityCount)
        {
            if (row == null) return;
            row["annotation_name"] = annotationName;
            row["note_name"] = noteName;
            row["tag_name"] = tagName;
            row["text"] = text;
            row["annotation_type"] = annotationType;
            row["owner_type"] = ownerType;
            row["leader_count"] = leaderCount;
            row["attached_entity_count"] = attachedEntityCount;
        }

        private static double[] ApproximateTextBounds(double x, double y,
            string text, ITextFormat format)
        {
            double height = 0.0035;
            double widthFactor = 1.0;
            try
            {
                if (format != null && IsFinite(format.CharHeight) &&
                    format.CharHeight > 0.0) height = format.CharHeight;
                if (format != null && IsFinite(format.WidthFactor) &&
                    format.WidthFactor > 0.0) widthFactor = format.WidthFactor;
            }
            catch { }
            double width = Math.Max(height * 0.5,
                height * 0.7 * widthFactor * Math.Max(1, text.Length));
            return NormalizeBounds(new[] { x - width / 2.0, y - height / 2.0,
                x + width / 2.0, y + height / 2.0 });
        }

        private static string SafeString(Func<string> read)
        {
            try { return read() ?? ""; } catch { return ""; }
        }

        private static JToken SectionDefinitionSha256(IView view)
        {
            try
            {
                double[] info = ToDoubleArray(view.GetSectionLineInfo2());
                Array rawStrings = view.GetSectionLineStrings() as Array;
                var strings = new JArray();
                if (rawStrings != null)
                    foreach (object value in rawStrings) strings.Add(Convert.ToString(value,
                        CultureInfo.InvariantCulture));
                if (info.Length == 0 && strings.Count == 0) return JValue.CreateNull();
                return CanonicalSha256(new JObject { ["line_info"] = new JArray(info),
                    ["strings"] = strings });
            }
            catch { return JValue.CreateNull(); }
        }

        private static int SafeInt(Func<int> read, int fallback)
        {
            try { return read(); } catch { return fallback; }
        }

        private static bool IsFinite(double value)
        {
            return !Double.IsNaN(value) && !Double.IsInfinity(value);
        }

        private static void CaptureLeaders(JArray objects, IAnnotation annotation,
            string ownerId, string viewName)
        {
            int count = 0;
            try { count = annotation.GetLeaderCount(); } catch { }
            for (int index = 0; index < count; index++)
            {
                double[] values = ToDoubleArray(annotation.GetLeaderPointsAtIndex(index));
                double[] bounds = BoundsFromTriples(values);
                if (bounds != null)
                {
                    JObject row = AddBound(objects, "leader:" + ownerId + ":" + index,
                        "leader_bounds", viewName, bounds,
                        "IAnnotation.GetLeaderPointsAtIndex", true);
                    if (row != null)
                    {
                        var points = new JArray();
                        for (int point = 0; point + 1 < values.Length; point += 3)
                            points.Add(new JArray(Math.Round(values[point], 12),
                                Math.Round(values[point + 1], 12)));
                        row["leader_points_sheet_m"] = points;
                        var display = annotation.GetDisplayData() as IDisplayData;
                        double arrowSize = 0.0;
                        int arrowCount = 0;
                        try { arrowCount = display != null ? display.GetArrowHeadCount() : 0; }
                        catch { }
                        for (int arrowIndex = 0; arrowIndex < arrowCount; arrowIndex++)
                        {
                            double[] arrow = ToDoubleArray(
                                display.GetArrowHeadAtIndex2(arrowIndex));
                            if (arrow.Length >= 8)
                                arrowSize = Math.Max(arrowSize,
                                    Math.Min(Math.Abs(arrow[6]), Math.Abs(arrow[7])));
                        }
                        if (arrowSize > 0.0)
                            row["arrow_size_m"] = Math.Round(arrowSize, 12);
                    }
                }
            }
        }

        private static void CaptureSectionSymbols(JArray objects, IView view,
            string viewName)
        {
            Array sections = null;
            try { sections = view.GetSectionLines() as Array; } catch { }
            if (sections == null) return;
            double[] viewPosition = new double[0];
            double[] sectionLineInfo = new double[0];
            try { viewPosition = ToDoubleArray(view.Position); } catch { }
            try { sectionLineInfo = ToDoubleArray(view.GetSectionLineInfo2()); }
            catch { }
            IList<SectionSymbolGeometry> native = viewPosition.Length >= 2
                ? LayoutDisplayGeometry.ParseSectionLineInfo2(sectionLineInfo,
                    viewPosition[0], viewPosition[1])
                : new List<SectionSymbolGeometry>();
            int index = 0;
            foreach (object item in sections)
            {
                var section = item as IDrSection;
                if (section == null) { index++; continue; }
                SectionSymbolGeometry geometry = index < native.Count
                    ? native[index] : null;
                var points = geometry == null
                    ? new List<double[]>() : geometry.Points.ToList();
                double[] bounds = BoundsFromPoints(points);
                if (bounds != null)
                    AddBound(objects, "section-symbol:" + viewName + ":" + index,
                        "section_symbol_bounds", viewName, bounds,
                        "IView.GetSectionLineInfo2", geometry.Exact);
                index++;
            }
        }

        private static void CaptureCenterElements(JArray objects, IView view,
            string viewName)
        {
            Array marks = null;
            try { marks = view.GetCenterMarks() as Array; } catch { }
            if (marks != null)
            {
                int index = 0;
                foreach (object item in marks)
                {
                    var mark = item as ICenterMark;
                    BoundResult bound = mark != null ? DisplayBounds(mark.GetAnnotation()) : null;
                    if (bound != null)
                        AddBound(objects, "center-mark:" + viewName + ":" + index,
                            "center_element_bounds", viewName, bound.Bounds,
                            bound.Api, bound.Exact);
                    index++;
                }
            }
            Array lines = null;
            try { lines = view.GetCenterLines() as Array; } catch { }
            if (lines != null)
            {
                int index = 0;
                foreach (object item in lines)
                {
                    var line = item as ICenterLine;
                    BoundResult bound = line != null ? DisplayBounds(line.GetAnnotation()) : null;
                    if (bound != null)
                        AddBound(objects, "center-line:" + viewName + ":" + index,
                            "center_element_bounds", viewName, bound.Bounds,
                            bound.Api, bound.Exact);
                    index++;
                }
            }
        }

        private static BoundResult DisplayBounds(IAnnotation annotation)
        {
            if (annotation == null) return null;
            var data = annotation.GetDisplayData() as IDisplayData;
            if (data == null) return null;
            var points = new List<double[]>();
            bool exact = true;
            int lineCount = 0;
            try { lineCount = data.GetLineCount(); } catch { }
            for (int index = 0; index < lineCount; index++)
            {
                double[] line = ToDoubleArray(data.GetLineAtIndex3(index));
                if (line.Length >= 10)
                {
                    points.Add(new[] { line[4], line[5] });
                    points.Add(new[] { line[7], line[8] });
                }
                else exact = false;
            }
            int arrowCount = 0;
            try { arrowCount = data.GetArrowHeadCount(); } catch { }
            for (int index = 0; index < arrowCount; index++)
            {
                double[] arrow = ToDoubleArray(data.GetArrowHeadAtIndex2(index));
                if (!LayoutDisplayGeometry.AddArrowHead(points, arrow)) exact = false;
            }
            int textCount = 0;
            try { textCount = data.GetTextCount(); } catch { }
            for (int index = 0; index < textCount; index++)
            {
                double[] position = ToDoubleArray(data.GetTextPositionAtIndex(index));
                if (position.Length < 2) { exact = false; continue; }
                double height = 0.0;
                double width = 0.0;
                string text = "";
                try { height = data.GetTextHeightAtIndex(index); } catch { }
                try { width = data.GetTextInBoxWidthAtIndex(index); } catch { }
                try { text = data.GetTextAtIndex(index) ?? ""; } catch { }
                int reference = -1;
                try { reference = data.GetTextRefPositionAtIndex(index); } catch { }
                if (height <= 0.0) { exact = false; continue; }
                if (width <= 0.0)
                {
                    width = Math.Max(height * 0.5,
                        height * 0.7 * Math.Max(1, text.Length));
                    exact = false;
                }
                double angle = 0.0;
                try { angle = data.GetTextAngleAtIndex(index); } catch { exact = false; }
                if (!LayoutDisplayGeometry.AddTextRectangle(points, position[0],
                    position[1], width, height, angle, reference)) exact = false;
            }
            if (points.Count == 0) return null;
            return new BoundResult
            {
                Bounds = NormalizeBounds(new[] { points.Min(p => p[0]),
                    points.Min(p => p[1]), points.Max(p => p[0]),
                    points.Max(p => p[1]) }),
                Api = "IAnnotation.GetDisplayData:GetLineAtIndex3," +
                    "GetArrowHeadAtIndex2,text metrics/ref-position",
                Exact = exact
            };
        }

        private static JArray BuildCapabilityRows(JObject before, JObject rebuilt,
            JObject reopened, double budget)
        {
            var result = new JArray();
            foreach (string capability in LayoutBoundaryProbeContract.CapabilityIds)
            {
                bool driftCapability = capability == "rebuild_drift" ||
                    capability == "save_reopen_drift";
                string category = driftCapability ? null : capability;
                IList<JObject> baseline = Records(before, category);
                IList<JObject> second = Records(
                    capability == "save_reopen_drift" ? reopened : rebuilt, category);
                IList<JObject> third = Records(reopened, category);
                bool observed = baseline.Count > 0;
                bool structured = observed && baseline.All(HasBounds) &&
                    second.All(HasBounds) && third.All(HasBounds);
                double? rebuildDrift = MaxDrift(baseline, Records(rebuilt, category));
                double? reopenDrift = MaxDrift(baseline, third);
                bool rebuildCompared = observed && rebuildDrift.HasValue;
                bool reopenCompared = observed && reopenDrift.HasValue;
                double? maximum = rebuildDrift.HasValue && reopenDrift.HasValue
                    ? Math.Max(rebuildDrift.Value, reopenDrift.Value) : (double?)null;
                bool within = maximum.HasValue && maximum.Value <= budget;
                bool exact = driftCapability || (baseline.Count > 0 &&
                    baseline.All(item => item.Value<bool>("exact")) &&
                    second.All(item => item.Value<bool>("exact")) &&
                    third.All(item => item.Value<bool>("exact")));
                bool supported = observed && structured && rebuildCompared &&
                    reopenCompared && within && exact;
                var limitations = new JArray();
                if (!observed) limitations.Add("object class was not present in this drawing");
                if (observed && !exact)
                    limitations.Add("native data requires an approximation; error evidence is not yet qualified");
                if (maximum.HasValue && !within)
                    limitations.Add("measured drift exceeds the request error budget");
                result.Add(new JObject
                {
                    ["id"] = capability,
                    ["status"] = supported ? "supported" : "planned",
                    ["checks"] = new JObject
                    {
                        ["native_api_invoked"] = true,
                        ["objects_observed"] = observed,
                        ["bounds_structured"] = structured,
                        ["rebuild_compared"] = rebuildCompared,
                        ["save_reopen_compared"] = reopenCompared,
                        ["within_error_budget"] = within
                    },
                    ["max_drift_m"] = maximum.HasValue
                        ? (JToken)Math.Round(maximum.Value, 12)
                        : JValue.CreateNull(),
                    ["evidence"] = observed
                        ? new JArray("objects=" + baseline.Count,
                            "rebuild_max_drift_m=" + FormatDrift(rebuildDrift),
                            "reopen_max_drift_m=" + FormatDrift(reopenDrift))
                        : new JArray(),
                    ["limitations"] = limitations
                });
            }
            return result;
        }

        private static IList<JObject> Records(JObject snapshot, string category)
        {
            IEnumerable<JObject> rows = ((JArray)snapshot["objects"]).OfType<JObject>();
            return (category == null ? rows : rows.Where(item =>
                String.Equals(item.Value<string>("category"), category,
                    StringComparison.Ordinal))).ToList();
        }

        private static bool HasBounds(JObject item)
        {
            var bounds = item["bounds"] as JArray;
            return bounds != null && bounds.Count == 4 && bounds.All(value =>
                value.Type == JTokenType.Float || value.Type == JTokenType.Integer);
        }

        private static double? MaxDrift(IList<JObject> first, IList<JObject> second)
        {
            var firstMap = first.ToDictionary(item => item.Value<string>("id"),
                StringComparer.Ordinal);
            var secondMap = second.ToDictionary(item => item.Value<string>("id"),
                StringComparer.Ordinal);
            if (firstMap.Count == 0 || firstMap.Count != secondMap.Count ||
                firstMap.Keys.Any(key => !secondMap.ContainsKey(key))) return null;
            double maximum = 0.0;
            foreach (string key in firstMap.Keys)
            {
                double[] left = firstMap[key]["bounds"].Values<double>().ToArray();
                double[] right = secondMap[key]["bounds"].Values<double>().ToArray();
                for (int index = 0; index < 4; index++)
                    maximum = Math.Max(maximum, Math.Abs(left[index] - right[index]));
            }
            return maximum;
        }

        private static string FormatDrift(double? value)
        {
            return value.HasValue ? Math.Round(value.Value, 12).ToString("R") : "unmatched";
        }

        private static JObject AddBound(JArray target, string id, string category,
            string view, double[] bounds, string api, bool exact)
        {
            if (bounds == null || bounds.Length != 4 || bounds.Any(value =>
                    Double.IsNaN(value) || Double.IsInfinity(value))) return null;
            var row = new JObject
            {
                ["id"] = id,
                ["category"] = category,
                ["view"] = view,
                ["bounds"] = new JArray(bounds.Select(value =>
                    Math.Round(value, 12))),
                ["source_api"] = api,
                ["exact"] = exact
            };
            target.Add(row);
            return row;
        }

        private static double[] NormalizeBounds(double[] values)
        {
            return new[] { Math.Min(values[0], values[2]),
                Math.Min(values[1], values[3]), Math.Max(values[0], values[2]),
                Math.Max(values[1], values[3]) };
        }

        private static double[] BoundsFromTriples(double[] values)
        {
            if (values == null || values.Length < 3) return null;
            var points = new List<double[]>();
            for (int index = 0; index + 1 < values.Length; index += 3)
                points.Add(new[] { values[index], values[index + 1] });
            return points.Count == 0 ? null : NormalizeBounds(new[]
            {
                points.Min(p => p[0]), points.Min(p => p[1]),
                points.Max(p => p[0]), points.Max(p => p[1])
            });
        }

        private static void AddNumericPairs(ICollection<double[]> points,
            double[] values)
        {
            if (values == null) return;
            for (int index = 0; index + 1 < values.Length; index += 2)
                if (IsFinite(values[index]) && IsFinite(values[index + 1]))
                    points.Add(new[] { values[index], values[index + 1] });
        }

        private static void AddNumericTriples(ICollection<double[]> points,
            double[] values)
        {
            if (values == null) return;
            for (int index = 0; index + 1 < values.Length; index += 3)
                if (IsFinite(values[index]) && IsFinite(values[index + 1]))
                    points.Add(new[] { values[index], values[index + 1] });
        }

        private static double[] BoundsFromPoints(IList<double[]> points)
        {
            return points == null || points.Count == 0 ? null : NormalizeBounds(new[]
            {
                points.Min(point => point[0]), points.Min(point => point[1]),
                points.Max(point => point[0]), points.Max(point => point[1])
            });
        }

        private static double[] ToDoubleArray(object value)
        {
            var array = value as Array;
            if (array == null) return new double[0];
            var result = new List<double>();
            foreach (object item in array)
            {
                try { result.Add(Convert.ToDouble(item)); } catch { }
            }
            return result.ToArray();
        }

        private static JArray BuildUpstreamRows(LayoutBoundaryProbeRequest request)
        {
            bool dimension = String.Equals(request.SourceKind,
                "verified_dimension_drawing", StringComparison.Ordinal);
            bool view = String.Equals(request.SourceKind,
                "verified_view_plan_drawing", StringComparison.Ordinal);
            return new JArray(
                Upstream(dimension ? "dimension_plan" : view ? "view_plan"
                    : "layout_fixture_manifest", request.Plan),
                Upstream(dimension ? "dimensioned_drawing" : view ? "view_drawing"
                    : "fixture_drawing",
                    request.Drawing),
                Upstream(dimension ? "dimension_verification_sidecar"
                    : view ? "view_verification_sidecar"
                    : "source_verification_sidecar", request.VerificationSidecar));
        }

        private static JObject Upstream(string role, LayoutBoundaryArtifact artifact)
        {
            return new JObject
            {
                ["role"] = role,
                ["path"] = artifact.Path,
                ["sha256_before"] = FileSha256(artifact.Path),
                ["sha256_after"] = new string('0', 64)
            };
        }

        private static void CompleteUpstreamRows(JArray rows)
        {
            foreach (JObject row in rows.OfType<JObject>())
                row["sha256_after"] = FileSha256(row.Value<string>("path"));
        }

        private static JObject SolidWorksIdentity(string revision)
        {
            string[] parts = (revision ?? "").Split('.');
            int majorRevision = 0;
            Int32.TryParse(parts.FirstOrDefault(), out majorRevision);
            int servicePack = 0;
            if (parts.Length > 1) Int32.TryParse(parts[1], out servicePack);
            return new JObject
            {
                ["major_version"] = majorRevision == 33 ? 2025 : 0,
                ["service_pack"] = "SP" + servicePack,
                ["revision"] = revision ?? ""
            };
        }

        internal static void RebuildForReadback(IModelDoc2 model)
        {
            try { model.ForceRebuild3(false); } catch { }
            try { model.EditRebuild3(); } catch { }
        }

        private static void AtomicWriteJson(string path, JToken value)
        {
            string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
            File.WriteAllText(temporary,
                value.ToString(Newtonsoft.Json.Formatting.Indented),
                new UTF8Encoding(false));
            if (File.Exists(path)) throw new IOException("output already exists: " + path);
            File.Move(temporary, path);
        }

        internal static string FileSha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var algorithm = SHA256.Create())
                return String.Concat(algorithm.ComputeHash(stream)
                    .Select(value => value.ToString("x2")));
        }

        internal static string CanonicalSha256(JToken value)
        {
            string canonical = Canonicalize(value).ToString(
                Newtonsoft.Json.Formatting.None);
            using (var algorithm = SHA256.Create())
                return String.Concat(algorithm.ComputeHash(Encoding.UTF8.GetBytes(canonical))
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

        private sealed class BoundResult
        {
            public double[] Bounds { get; set; }
            public string Api { get; set; }
            public bool Exact { get; set; }
        }
    }
}
