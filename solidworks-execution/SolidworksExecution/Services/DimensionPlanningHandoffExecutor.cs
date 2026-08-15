using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// F1 read-only native extractor.  It never saves a SolidWorks document and
    /// publishes the single immutable handoff only after the upstream hash and
    /// dirty-state ledger has been checked a second time.
    /// </summary>
    internal sealed class DimensionPlanningHandoffExecutor
    {
        private const string OutputName = "dimension-planning-handoff.json";
        private const string ViewNamePrefix = "Q3DS_VP_";
        private readonly ISldWorks _solidWorks;

        public DimensionPlanningHandoffExecutor(ISldWorks solidWorks)
        {
            _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks");
        }

        public JObject Execute(DimensionPlanningHandoffRequest request)
        {
            if (request == null) throw new ArgumentNullException("request");
            Directory.CreateDirectory(request.PublicationDirectory);
            string outputPath = Path.Combine(request.PublicationDirectory, OutputName);
            if (File.Exists(outputPath))
                throw new IOException("refusing to overwrite F1 handoff: " + outputPath);

            string requestHash = DimensionPlanningHandoffContract.CanonicalSha256(
                request.SourceRequest);
            var ledger = BuildLedger(request);
            string previousTitle = ActiveTitle();
            bool sourceWasOpen = false;
            IModelDoc2 drawingModel = null;
            IModelDoc2 sourceModel = null;
            try
            {
                if (_solidWorks.GetOpenDocumentByName(request.VerifiedDrawing.Path) != null)
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_DRAWING_ALREADY_OPEN: read-only extraction " +
                        "requires the verified drawing to be closed.");
                sourceModel = _solidWorks.GetOpenDocumentByName(
                    request.SourceModel.Path) as IModelDoc2;
                sourceWasOpen = sourceModel != null;
                EnsureClean(sourceModel, "source model");

                int errors = 0;
                int warnings = 0;
                drawingModel = _solidWorks.OpenDoc6(request.VerifiedDrawing.Path,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    "", ref errors, ref warnings) as IModelDoc2;
                var drawing = drawingModel as IDrawingDoc;
                if (drawing == null || !drawingModel.IsOpenedReadOnly() ||
                    !PathEquals(drawingModel.GetPathName(), request.VerifiedDrawing.Path))
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_READONLY_OPEN_FAILED: errors=" + errors +
                        " warnings=" + warnings);

                sourceModel = _solidWorks.GetOpenDocumentByName(
                    request.SourceModel.Path) as IModelDoc2;
                if (sourceModel == null)
                {
                    errors = 0;
                    warnings = 0;
                    sourceModel = _solidWorks.OpenDoc6(request.SourceModel.Path,
                        (int)swDocumentTypes_e.swDocPART,
                        (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                        (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                        "", ref errors, ref warnings) as IModelDoc2;
                }
                if (sourceModel == null ||
                    !PathEquals(sourceModel.GetPathName(), request.SourceModel.Path))
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_MODEL_OPEN_FAILED");
                EnsureClean(sourceModel, "source model");
                EnsureClean(drawingModel, "verified drawing");

                JObject drawingContext;
                JArray measurements;
                JArray views = ReadViews(drawing, sourceModel,
                    request.ViewPlanValue, request.VerificationSidecarValue,
                    out drawingContext, out measurements);
                drawingContext["path"] = request.VerifiedDrawing.Path;
                JArray modelDimensions = ReadModelDimensions(sourceModel);
                JArray pmi = ReadModelAnnotations(sourceModel);

                EnsureClean(sourceModel, "source model after readback");
                EnsureClean(drawingModel, "verified drawing after readback");
                AddModelDimensionImportCandidates(drawingModel, drawing, sourceModel,
                    views, modelDimensions);
                JArray features = ReadManufacturingFeatures(sourceModel, modelDimensions);

                Close(drawingModel);
                drawingModel = null;
                if (!sourceWasOpen)
                {
                    Close(sourceModel);
                    sourceModel = null;
                }
                else EnsureClean(sourceModel, "pre-existing source model");

                CompleteLedger(ledger);
                EnsureLedgerUnchanged(ledger);
                JObject result = new JObject
                {
                    ["protocol_id"] = "solidworks-dimension-planning-handoff",
                    ["schema_version"] = "1.0",
                    ["handoff_id"] = "DMH-" + requestHash.Substring(0, 16),
                    ["created_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture),
                    ["status"] = "ready",
                    ["source_request_sha256"] = requestHash,
                    ["upstream_artifacts"] = ledger,
                    ["source_model"] = new JObject
                    {
                        ["path"] = request.SourceModel.Path,
                        ["sha256"] = request.SourceModel.Sha256,
                        ["configuration"] =
                            request.ViewPlanValue.Value<string>("configuration"),
                        ["save_flag"] = false,
                        ["persistent_reference_domain"] = "source_model"
                    },
                    ["drawing_context"] = drawingContext,
                    ["views"] = views,
                    ["model_driven_dimensions"] = modelDimensions,
                    ["pmi_annotations"] = pmi,
                    ["manufacturing_features"] = features,
                    ["approved_user_inputs"] =
                        request.ApprovedUserInputs.DeepClone(),
                    ["reference_measurements"] = measurements,
                    ["dimension_zones"] =
                        request.ViewPlanValue["dimension_zones"].DeepClone(),
                    ["limitations"] = new JObject
                    {
                        ["annotation_text_bounds"] = "unsupported_exact",
                        ["reference_measurements_are_manufacturing_requirements"] = false
                    },
                    ["source_immutability"] = new JObject
                    {
                        ["drawing_opened_read_only"] = true,
                        ["source_documents_clean"] = true,
                        ["hashes_unchanged"] = true
                    }
                };
                AtomicWriteJson(outputPath, result);
                return new JObject
                {
                    ["status"] = "ready",
                    ["handoff_path"] = outputPath,
                    ["handoff_sha256"] =
                        DimensionPlanningHandoffContract.FileSha256(outputPath),
                    ["handoff_id"] = result["handoff_id"].DeepClone(),
                    ["view_count"] = views.Count,
                    ["model_dimension_count"] = modelDimensions.Count,
                    ["pmi_annotation_count"] = pmi.Count,
                    ["manufacturing_feature_count"] = features.Count,
                    ["model_dimension_import_candidate_count"] =
                        modelDimensions.OfType<JObject>().Sum(row =>
                            (row["import_candidates"] as JArray ?? new JArray()).Count)
                };
            }
            finally
            {
                Close(drawingModel);
                if (!sourceWasOpen) Close(sourceModel);
                RestoreActive(previousTitle);
            }
        }

        private JArray ReadViews(IDrawingDoc drawing, IModelDoc2 sourceModel,
            JObject plan, JObject verificationSidecar, out JObject drawingContext,
            out JArray measurements)
        {
            measurements = new JArray();
            var sheet = drawing.GetCurrentSheet() as ISheet;
            double[] sheetProperties = sheet != null
                ? ToDoubleArray(sheet.GetProperties2()) : new double[0];
            double width = sheetProperties.Length >= 7 ? sheetProperties[5] : 0.0;
            double height = sheetProperties.Length >= 7 ? sheetProperties[6] : 0.0;
            double numerator = plan["sheet_scale"] != null
                ? plan["sheet_scale"].Value<double>("numerator") : 1.0;
            double denominator = plan["sheet_scale"] != null
                ? plan["sheet_scale"].Value<double>("denominator") : 1.0;
            drawingContext = new JObject
            {
                ["path"] = plan.Value<string>("drawing_path") ?? "",
                ["sheet_name"] = sheet != null ? sheet.GetName() : "Sheet1",
                ["sheet_bounds_m"] = new JArray(0.0, 0.0, Round(width), Round(height)),
                ["projection_method"] = plan.Value<string>("projection_method"),
                ["sheet_scale"] = Round(numerator / denominator)
            };

            var live = new Dictionary<string, IView>(StringComparer.Ordinal);
            IView cursor = drawing.GetFirstView() as IView;
            cursor = cursor != null ? cursor.GetNextView() as IView : null;
            int guard = 0;
            while (cursor != null && guard++ < 2000)
            {
                live[cursor.Name ?? ""] = cursor;
                cursor = cursor.GetNextView() as IView;
            }
            if (guard >= 2000) throw new InvalidOperationException(
                "DIMENSION_HANDOFF_VIEW_ITERATION_LIMIT");

            var verifiedNames = new Dictionary<string, string>(StringComparer.Ordinal);
            var verifiedRows = verificationSidecar != null
                ? verificationSidecar.SelectToken("verification.views") as JArray : null;
            if (verifiedRows != null)
                foreach (JObject row in verifiedRows.OfType<JObject>())
                {
                    string id = row.Value<string>("id");
                    string name = row.Value<string>("name");
                    if (!String.IsNullOrWhiteSpace(id) &&
                        !String.IsNullOrWhiteSpace(name)) verifiedNames[id] = name;
                }

            var result = new JArray();
            var plannedViews = plan["views"] as JArray;
            if (plannedViews == null || plannedViews.Count == 0)
                throw new InvalidOperationException("ViewPlan has no views.");
            foreach (JObject planned in plannedViews.OfType<JObject>())
            {
                string viewId = planned.Value<string>("id");
                IView view = null;
                string verifiedName;
                bool found = !String.IsNullOrWhiteSpace(viewId) &&
                    live.TryGetValue(ViewNamePrefix + viewId, out view);
                if (!found && !String.IsNullOrWhiteSpace(viewId) &&
                    verifiedNames.TryGetValue(viewId, out verifiedName))
                    found = live.TryGetValue(verifiedName, out view);
                if (!found)
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_PLANNED_VIEW_MISSING: " + viewId);
                JArray geometry = ReadProjectedGeometry(view, sourceModel, viewId,
                    measurements);
                double[] outline = ToDoubleArray(view.GetOutline());
                if (outline.Length < 4)
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_VIEW_OUTLINE_MISSING: " + viewId);
                result.Add(new JObject
                {
                    ["view_id"] = viewId,
                    ["solidworks_name"] = view.Name,
                    ["bounds_sheet_m"] = Rounded(outline.Take(4)),
                    ["referenced_model"] = view.GetReferencedModelName() ??
                        sourceModel.GetPathName(),
                    ["configuration"] = view.ReferencedConfiguration ??
                        plan.Value<string>("configuration"),
                    ["projected_geometry"] = geometry,
                    ["existing_annotations"] = ReadExistingAnnotations(view, viewId)
                });
            }
            return result;
        }

        private JArray ReadProjectedGeometry(IView view, IModelDoc2 sourceModel,
            string viewId, JArray measurements)
        {
            var result = new JArray();
            int ordinaryEntityIndex = 0;
            Array components = view.GetVisibleComponents() as Array;
            if (components == null) return result;
            foreach (object componentObject in components)
            {
                var component = componentObject as Component2;
                Array entities = component != null
                    ? view.GetVisibleEntities2(component, 1) as Array : null;
                if (entities == null) continue;
                foreach (object entity in entities)
                {
                    var edge = entity as IEdge;
                    if (edge == null) continue;
                    string persist = PersistReference(sourceModel.Extension, edge);
                    if (String.IsNullOrEmpty(persist))
                        throw new InvalidOperationException(
                            "DIMENSION_HANDOFF_PERSIST_REFERENCE_MISSING: " + viewId);
                    string entityId = "GE-" + StableToken(viewId + "|entity|" +
                        ordinaryEntityIndex.ToString(CultureInfo.InvariantCulture) + "|" +
                        persist);
                    ordinaryEntityIndex++;
                    var curve = edge.GetCurve() as ICurve;
                    string kind = "other_curve";
                    JArray geometry = new JArray();
                    double measurement = Double.NaN;
                    string measurementKind = null;
                    if (curve != null && curve.IsLine())
                    {
                        double[] first;
                        double[] second;
                        if (TryProjectLine(view, edge, out first, out second))
                        {
                            kind = "line";
                            geometry = Rounded(new[]
                                { first[0], first[1], second[0], second[1] });
                            measurement = Math.Sqrt(
                                Math.Pow(second[0] - first[0], 2.0) +
                                Math.Pow(second[1] - first[1], 2.0));
                            measurementKind = "projected_line_length";
                        }
                    }
                    else if (curve != null && curve.IsCircle())
                    {
                        double[] circle = ToDoubleArray(curve.CircleParams);
                        kind = IsFullCircle(edge) ? "circle" : "arc";
                        if (circle.Length >= 7)
                        {
                            double[] center;
                            if (TryProjectPoint(view, circle.Take(3).ToArray(), out center))
                                geometry = Rounded(new[]
                                    { center[0], center[1], circle[6] * view.ScaleDecimal });
                            measurement = Math.Abs(circle[6] * view.ScaleDecimal);
                            measurementKind = "projected_circle_radius";
                        }
                    }
                    result.Add(new JObject
                    {
                        ["entity_id"] = entityId,
                        ["entity_kind"] = kind,
                        ["model_persistent_reference"] = persist,
                        ["persistent_reference_kind"] = "entity",
                        ["geometry_sheet_m"] = geometry,
                        ["source_tier"] = "model_or_pmi"
                    });
                    if (measurementKind != null && !Double.IsNaN(measurement))
                        measurements.Add(new JObject
                        {
                            ["measurement_id"] = "RM-" + StableToken(
                                viewId + "|" + measurementKind + "|" + entityId),
                            ["view_id"] = viewId,
                            ["kind"] = measurementKind,
                            ["value_si"] = Round(measurement),
                            ["entity_ids"] = new JArray(entityId),
                            ["source_tier"] = "reference_geometry_measurement",
                            ["manufacturing_requirement"] = false
                        });
                }

                Array silhouettes = component != null
                    ? view.GetVisibleEntities2(component,
                        (int)swViewEntityType_e.swViewEntityType_SilhouetteEdge) as Array
                    : null;
                if (silhouettes == null) continue;
                foreach (object entity in silhouettes)
                {
                    var silhouette = entity as ISilhouetteEdge;
                    if (silhouette == null) continue;
                    var face = silhouette.GetFace() as IFace2;
                    string persist = PersistReference(sourceModel.Extension, face);
                    if (String.IsNullOrEmpty(persist))
                        throw new InvalidOperationException(
                            "DIMENSION_HANDOFF_SILHOUETTE_FACE_REFERENCE_MISSING: " +
                            viewId);
                    string entityId = "GE-" + StableToken(viewId +
                        "|silhouette|" + persist + "|" + result.Count);
                    var curve = silhouette.GetCurve() as ICurve;
                    string kind = "other_curve";
                    JArray geometry = new JArray();
                    string measurementKind = null;
                    double measurement = Double.NaN;
                    var startPoint = silhouette.GetStartPoint() as IMathPoint;
                    var endPoint = silhouette.GetEndPoint() as IMathPoint;
                    double[] first;
                    double[] second;
                    if (curve != null && curve.IsLine() && startPoint != null &&
                        endPoint != null && TryProjectPoint(view,
                            startPoint.ArrayData as double[], out first) &&
                        TryProjectPoint(view, endPoint.ArrayData as double[], out second))
                    {
                        kind = "silhouette_line";
                        geometry = Rounded(new[]
                            { first[0], first[1], second[0], second[1] });
                        measurement = Math.Sqrt(
                            Math.Pow(second[0] - first[0], 2.0) +
                            Math.Pow(second[1] - first[1], 2.0));
                        measurementKind = "projected_line_length";
                    }
                    else if (curve != null && curve.IsCircle())
                    {
                        double[] circle = ToDoubleArray(curve.CircleParams);
                        kind = startPoint == null && endPoint == null
                            ? "silhouette_circle" : "silhouette_arc";
                        if (circle.Length >= 7)
                        {
                            double[] center;
                            if (TryProjectPoint(view, circle.Take(3).ToArray(),
                                    out center))
                                geometry = Rounded(new[] { center[0], center[1],
                                    circle[6] * view.ScaleDecimal });
                            measurement = Math.Abs(circle[6] * view.ScaleDecimal);
                            measurementKind = "projected_circle_radius";
                        }
                    }
                    if (geometry.Count == 0) continue;
                    result.Add(new JObject
                    {
                        ["entity_id"] = entityId,
                        ["entity_kind"] = kind,
                        ["model_persistent_reference"] = persist,
                        ["persistent_reference_kind"] = "backing_face",
                        ["geometry_sheet_m"] = geometry,
                        ["source_tier"] = "model_or_pmi"
                    });
                    if (measurementKind != null && !Double.IsNaN(measurement))
                        measurements.Add(new JObject
                        {
                            ["measurement_id"] = "RM-" + StableToken(
                                viewId + "|silhouette|" + measurementKind + "|" +
                                persist + "|" + result.Count),
                            ["view_id"] = viewId,
                            ["kind"] = measurementKind,
                            ["value_si"] = Round(measurement),
                            ["entity_ids"] = new JArray(entityId),
                            ["source_tier"] = "reference_geometry_measurement",
                            ["manufacturing_requirement"] = false
                        });
                }
            }
            return result;
        }

        private static JArray ReadExistingAnnotations(IView view, string viewId)
        {
            var result = new JArray();
            Array annotations = view.GetAnnotations() as Array;
            if (annotations == null) return result;
            int index = 0;
            foreach (object value in annotations)
            {
                var annotation = value as IAnnotation;
                if (annotation == null) continue;
                double[] position = ToDoubleArray(annotation.GetPosition());
                result.Add(new JObject
                {
                    ["annotation_id"] = "AN-" + StableToken(viewId + "|" +
                        index.ToString(CultureInfo.InvariantCulture) + "|" +
                        (annotation.GetName() ?? "")),
                    ["type"] = annotation.GetType(),
                    ["position_sheet_m"] = Rounded(position),
                    ["display_envelope_sheet_m"] = ApproximateDisplayEnvelope(annotation),
                    ["text_bounds_exact"] = false
                });
                index++;
            }
            return result;
        }

        private static JToken ApproximateDisplayEnvelope(IAnnotation annotation)
        {
            try
            {
                var data = annotation.GetDisplayData() as IDisplayData;
                if (data == null || data.GetTextCount() == 0) return JValue.CreateNull();
                double minX = Double.PositiveInfinity;
                double minY = Double.PositiveInfinity;
                double maxX = Double.NegativeInfinity;
                double maxY = Double.NegativeInfinity;
                for (int index = 0; index < data.GetTextCount(); index++)
                {
                    double[] point = ToDoubleArray(data.GetTextPositionAtIndex(index));
                    if (point.Length < 2) continue;
                    double height = Math.Abs(data.GetTextHeightAtIndex(index));
                    minX = Math.Min(minX, point[0] - height);
                    maxX = Math.Max(maxX, point[0] + height);
                    minY = Math.Min(minY, point[1] - height);
                    maxY = Math.Max(maxY, point[1] + height);
                }
                return Double.IsInfinity(minX) ? JValue.CreateNull() :
                    (JToken)Rounded(new[] { minX, minY, maxX, maxY });
            }
            catch { return JValue.CreateNull(); }
        }

        private static JArray ReadModelDimensions(IModelDoc2 model)
        {
            var result = new JArray();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            IFeature feature = model.FirstFeature() as IFeature;
            int featureGuard = 0;
            while (feature != null && featureGuard++ < 100000)
            {
                object current = null;
                try { current = feature.GetFirstDisplayDimension(); } catch { }
                int dimensionGuard = 0;
                while (current != null && dimensionGuard++ < 10000)
                {
                    var display = current as IDisplayDimension;
                    object next = null;
                    try { next = feature.GetNextDisplayDimension(current); } catch { }
                    var dimension = display != null
                        ? display.GetDimension2(0) as IDimension : null;
                    if (dimension != null)
                    {
                        string fullName = dimension.FullName ?? dimension.Name ?? "";
                        if (!String.IsNullOrWhiteSpace(fullName) && seen.Add(fullName))
                        {
                            double value = DimensionValue(dimension);
                            if (!Double.IsNaN(value) && !Double.IsInfinity(value))
                            {
                                bool markedForDrawing = false;
                                bool referenceDimension = false;
                                int nativeType = 0;
                                IFeature owner = null;
                                try { markedForDrawing = display.MarkedForDrawing; } catch { }
                                try { referenceDimension = display.IsReferenceDim(); } catch { }
                                try { nativeType = display.Type2; } catch { }
                                try { owner = dimension.GetFeatureOwner() as IFeature; } catch { }
                                string ownerName = owner != null ? owner.Name ?? "" : "";
                                string ownerType = owner != null
                                    ? owner.GetTypeName2() ?? owner.GetTypeName() ?? "" : "";
                                string ownerId = owner != null
                                    ? "MF-" + StableToken(ownerName + "|" + ownerType) : null;
                                string ownerReference = owner != null
                                    ? PersistReference(model.Extension, owner) : null;
                                result.Add(new JObject
                                {
                                    ["dimension_id"] = "MD-" + StableToken(fullName),
                                    ["full_name"] = fullName,
                                    ["value_si"] = Round(value),
                                    ["native_type"] = nativeType,
                                    ["marked_for_drawing"] = markedForDrawing,
                                    ["reference_dimension"] = referenceDimension,
                                    ["manufacturing_requirement"] =
                                        markedForDrawing && !referenceDimension,
                                    ["owner_feature_id"] = ownerId != null
                                        ? (JToken)ownerId : JValue.CreateNull(),
                                    ["owner_feature_name"] = owner != null
                                        ? (JToken)ownerName : JValue.CreateNull(),
                                    ["owner_feature_type"] = owner != null
                                        ? (JToken)ownerType : JValue.CreateNull(),
                                    ["owner_feature_persistent_reference"] =
                                        ownerReference != null
                                        ? (JToken)ownerReference : JValue.CreateNull(),
                                    ["import_candidates"] = new JArray(),
                                    ["source_tier"] = "model_or_pmi",
                                    ["provenance"] = "model_driven_dimension"
                                });
                            }
                        }
                    }
                    current = next;
                }
                feature = feature.GetNextFeature() as IFeature;
            }
            if (featureGuard >= 100000) throw new InvalidOperationException(
                "DIMENSION_HANDOFF_FEATURE_ITERATION_LIMIT");
            return result;
        }

        private static void AddModelDimensionImportCandidates(IModelDoc2 drawingModel,
            IDrawingDoc drawing, IModelDoc2 sourceModel, JArray views,
            JArray modelDimensions)
        {
            var dimensionsByName = modelDimensions.OfType<JObject>()
                .Where(row => !String.IsNullOrWhiteSpace(row.Value<string>("full_name")))
                .ToDictionary(row => row.Value<string>("full_name"), row => row,
                    StringComparer.Ordinal);
            var viewRowsByName = views.OfType<JObject>()
                .Where(row => !String.IsNullOrWhiteSpace(
                    row.Value<string>("solidworks_name")))
                .ToDictionary(row => row.Value<string>("solidworks_name"), row => row,
                    StringComparer.Ordinal);
            var baselineNames = new HashSet<string>(StringComparer.Ordinal);
            foreach (JObject viewRow in viewRowsByName.Values)
            {
                IView view = FindView(drawing,
                    viewRow.Value<string>("solidworks_name"));
                if (view == null) continue;
                object cursor = view.GetFirstDisplayDimension5();
                int guard = 0;
                while (cursor != null && guard++ < 10000)
                {
                    var display = cursor as IDisplayDimension;
                    cursor = display != null ? display.GetNext5() : null;
                    string name = DisplaySelectionName(display);
                    if (!String.IsNullOrWhiteSpace(name)) baselineNames.Add(name);
                }
                if (guard >= 10000) throw new InvalidOperationException(
                    "DIMENSION_HANDOFF_BASELINE_DIMENSION_ITERATION_LIMIT");
            }

            drawingModel.ClearSelection2(true);
            int types = (int)swInsertAnnotation_e.swInsertDimensionsMarkedForDrawing |
                (int)swInsertAnnotation_e.swInsertDimensionsNotMarkedForDrawing |
                (int)swInsertAnnotation_e.swInsertHoleWizardProfileDimensions |
                (int)swInsertAnnotation_e.swInsertHoleWizardLocationDimensions |
                (int)swInsertAnnotation_e.swInsertholeCallout |
                (int)swInsertAnnotation_e.swInsertTolerancedDims;
            drawing.InsertModelAnnotations3(
                (int)swImportModelItemsSource_e.swImportModelItemsFromEntireModel,
                types, true, false, false, true);
            drawingModel.ForceRebuild3(false);

            var importedAnnotations = new List<IAnnotation>();
            foreach (KeyValuePair<string, JObject> pair in viewRowsByName)
            {
                IView view = FindView(drawing, pair.Key);
                if (view == null) continue;
                JObject viewRow = pair.Value;
                var entityIdsByReference = viewRow["projected_geometry"]
                    .OfType<JObject>()
                    .Where(row => !String.IsNullOrWhiteSpace(
                        row.Value<string>("model_persistent_reference")))
                    .GroupBy(row => row.Value<string>("model_persistent_reference"),
                        StringComparer.Ordinal)
                    .ToDictionary(group => group.Key,
                        group => group.Select(row => row.Value<string>("entity_id"))
                            .Where(value => !String.IsNullOrWhiteSpace(value)).ToList(),
                        StringComparer.Ordinal);
                object cursor = view.GetFirstDisplayDimension5();
                int guard = 0;
                while (cursor != null && guard++ < 10000)
                {
                    var display = cursor as IDisplayDimension;
                    cursor = display != null ? display.GetNext5() : null;
                    if (display == null) continue;
                    string selectionName = DisplaySelectionName(display);
                    if (baselineNames.Contains(selectionName)) continue;
                    IAnnotation annotation = display.GetAnnotation() as IAnnotation;
                    if (annotation != null) importedAnnotations.Add(annotation);
                    IDimension dimension = null;
                    try { dimension = display.GetDimension2(0) as IDimension; } catch { }
                    string fullName = dimension != null ? dimension.FullName ?? "" : "";
                    JObject source;
                    if (!dimensionsByName.TryGetValue(fullName, out source)) continue;
                    var references = new List<string>();
                    Array attached = annotation != null
                        ? annotation.GetAttachedEntities3() as Array : null;
                    if (attached != null)
                        foreach (object entity in attached)
                        {
                            byte[] bytes = ToByteArray(
                                sourceModel.Extension.GetPersistReference3(entity));
                            if (bytes != null && bytes.Length > 0)
                                references.Add(Convert.ToBase64String(bytes));
                        }
                    if (references.Count == 0) continue;
                    var consumedByReference = new Dictionary<string, int>(
                        StringComparer.Ordinal);
                    var entityIds = new JArray();
                    bool complete = true;
                    foreach (string reference in references)
                    {
                        List<string> candidates;
                        int used;
                        consumedByReference.TryGetValue(reference, out used);
                        if (!entityIdsByReference.TryGetValue(reference, out candidates) ||
                            candidates.Count == 0)
                        {
                            complete = false;
                            break;
                        }
                        entityIds.Add(candidates[Math.Min(used, candidates.Count - 1)]);
                        consumedByReference[reference] = used + 1;
                    }
                    double[] position = annotation != null
                        ? annotation.GetPosition() as double[] : null;
                    if (!complete || position == null || position.Length < 2 ||
                        position.Take(2).Any(value => Double.IsNaN(value) ||
                            Double.IsInfinity(value))) continue;
                    (source["import_candidates"] as JArray).Add(new JObject
                    {
                        ["view_id"] = viewRow.Value<string>("view_id"),
                        ["native_type"] = display.Type2,
                        ["is_hole_callout"] = display.IsHoleCallout(),
                        ["position_sheet_m"] = new JArray(
                            Round(position[0]), Round(position[1])),
                        ["attachment_entity_ids"] = entityIds,
                        ["model_persistent_references"] = new JArray(references)
                    });
                }
                if (guard >= 10000) throw new InvalidOperationException(
                    "DIMENSION_HANDOFF_IMPORTED_DIMENSION_ITERATION_LIMIT");
            }
            foreach (IAnnotation annotation in importedAnnotations)
            {
                drawingModel.ClearSelection2(true);
                if (annotation == null || !annotation.Select3(false, null))
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_IMPORT_PROBE_DELETE_FAILED");
                drawingModel.EditDelete();
            }
            drawingModel.ClearSelection2(true);
            foreach (JObject dimension in modelDimensions.OfType<JObject>())
            {
                var candidates = dimension["import_candidates"] as JArray;
                dimension["manufacturing_requirement"] =
                    dimension.Value<bool>("marked_for_drawing") &&
                    !dimension.Value<bool>("reference_dimension") &&
                    candidates != null && candidates.Count > 0;
            }
        }

        private static IView FindView(IDrawingDoc drawing, string name)
        {
            object cursor = drawing.GetFirstView();
            int guard = 0;
            while (cursor != null && guard++ < 2000)
            {
                var view = cursor as IView;
                cursor = view != null ? view.GetNextView() : null;
                if (view != null && String.Equals(view.Name, name,
                    StringComparison.Ordinal)) return view;
            }
            return null;
        }

        private static string DisplaySelectionName(IDisplayDimension display)
        {
            try { return display != null ? display.GetNameForSelection() ?? "" : ""; }
            catch { return ""; }
        }

        private static byte[] ToByteArray(object raw)
        {
            byte[] bytes = raw as byte[];
            Array array = raw as Array;
            if (bytes != null || array == null) return bytes;
            bytes = new byte[array.Length];
            for (int index = 0; index < array.Length; index++)
                bytes[index] = Convert.ToByte(array.GetValue(index),
                    CultureInfo.InvariantCulture);
            return bytes;
        }

        private static JArray ReadModelAnnotations(IModelDoc2 model)
        {
            var result = new JArray();
            Array annotations = model.Extension.GetAnnotations() as Array;
            if (annotations == null) return result;
            int index = 0;
            foreach (object value in annotations)
            {
                var annotation = value as IAnnotation;
                if (annotation == null) continue;
                var references = new JArray();
                Array attached = annotation.GetAttachedEntities3() as Array;
                if (attached != null)
                    foreach (object entity in attached)
                    {
                        string persist = PersistReference(model.Extension, entity);
                        if (!String.IsNullOrEmpty(persist)) references.Add(persist);
                    }
                string name = annotation.GetName() ?? "";
                result.Add(new JObject
                {
                    ["annotation_id"] = "PMI-" + StableToken(index + "|" + name),
                    ["type"] = annotation.GetType(),
                    ["source_tier"] = "model_or_pmi",
                    ["provenance"] = "model_pmi",
                    ["persistent_references"] = references
                });
                index++;
            }
            return result;
        }

        private static JArray ReadManufacturingFeatures(IModelDoc2 model,
            JArray modelDimensions)
        {
            var result = new JArray();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            IFeature feature = model.FirstFeature() as IFeature;
            int guard = 0;
            while (feature != null && guard++ < 100000)
            {
                string type = feature.GetTypeName2() ?? feature.GetTypeName() ?? "";
                string classification = ClassifyFeature(type, feature.Name);
                if (classification != null)
                {
                    string featureId = "MF-" + StableToken(feature.Name + "|" + type);
                    string persist = PersistReference(model.Extension, feature);
                    if (String.IsNullOrEmpty(persist))
                        throw new InvalidOperationException(
                            "DIMENSION_HANDOFF_FEATURE_REFERENCE_MISSING: " +
                            feature.Name);
                    if (seen.Add(featureId)) result.Add(new JObject
                    {
                        ["feature_id"] = featureId,
                        ["name"] = feature.Name,
                        ["type_name"] = type,
                        ["classification"] = classification,
                        ["persistent_reference"] = persist,
                        ["source_tier"] = "model_or_pmi"
                    });
                }
                feature = feature.GetNextFeature() as IFeature;
            }
            if (guard >= 100000) throw new InvalidOperationException(
                "DIMENSION_HANDOFF_FEATURE_ITERATION_LIMIT");
            return result;
        }

        private static string ClassifyFeature(string type, string name)
        {
            string value = ((type ?? "") + " " + (name ?? "")).ToLowerInvariant();
            if (value.Contains("hole")) return "hole";
            if (value.Contains("slot")) return "slot";
            if (value.Contains("cirpattern") || value.Contains("circularpattern"))
                return "circular_pattern";
            if (value.Contains("lpattern") || value.Contains("linearpattern"))
                return "linear_pattern";
            // Every non-reference DimensionPlan dimension must bind a frozen model feature.
            // Keeping only holes and patterns made ordinary extruded/revolved parts
            // undimensionable despite their trusted model-driven dimensions.  Preserve the
            // specialised classifications above and include body-forming/editing features as
            // neutral bindings; sketches, planes, folders and UI nodes remain excluded.
            string featureType = (type ?? "").ToLowerInvariant();
            string[] modelFeatureTokens =
            {
                "boss", "extrude", "cut", "revolve", "sweep", "loft",
                "boundary", "rib", "shell", "draft", "fillet", "chamfer",
                "dome", "thicken", "deform", "flex", "combine", "moveface",
                "deleteface", "split", "mirrorpattern", "bodymove"
            };
            if (modelFeatureTokens.Any(featureType.Contains)) return "model_feature";
            return null;
        }

        private bool TryProjectLine(IView view, IEdge edge,
            out double[] first, out double[] second)
        {
            first = null;
            second = null;
            var a = edge.GetStartVertex() as IVertex;
            var b = edge.GetEndVertex() as IVertex;
            return a != null && b != null &&
                TryProjectPoint(view, a.GetPoint() as double[], out first) &&
                TryProjectPoint(view, b.GetPoint() as double[], out second);
        }

        private bool TryProjectPoint(IView view, double[] modelPoint,
            out double[] sheetPoint)
        {
            sheetPoint = null;
            try
            {
                var utility = _solidWorks.GetMathUtility() as IMathUtility;
                var point = utility != null && modelPoint != null
                    ? utility.CreatePoint(modelPoint) as IMathPoint : null;
                var transformed = point != null
                    ? point.MultiplyTransform(view.ModelToViewTransform) as IMathPoint
                    : null;
                sheetPoint = transformed != null
                    ? transformed.ArrayData as double[] : null;
                return sheetPoint != null && sheetPoint.Length >= 2 &&
                    sheetPoint.All(value => !Double.IsNaN(value) &&
                        !Double.IsInfinity(value));
            }
            catch { return false; }
        }

        private static bool IsFullCircle(IEdge edge)
        {
            try { return edge.GetStartVertex() == null && edge.GetEndVertex() == null; }
            catch { return false; }
        }

        private static double DimensionValue(IDimension dimension)
        {
            try
            {
                double[] values = ToDoubleArray(dimension.GetSystemValue3(1, null));
                if (values.Length > 0) return values[0];
            }
            catch { }
            try { return dimension.Value; }
            catch { return Double.NaN; }
        }

        private static JArray BuildLedger(DimensionPlanningHandoffRequest request)
        {
            return new JArray(
                LedgerRow("view_plan", request.ViewPlan),
                LedgerRow("verified_drawing", request.VerifiedDrawing),
                LedgerRow("verification_sidecar", request.VerificationSidecar),
                LedgerRow("source_model", request.SourceModel));
        }

        private static JObject LedgerRow(string role,
            DimensionPlanningArtifact artifact)
        {
            return new JObject
            {
                ["role"] = role,
                ["path"] = artifact.Path,
                ["sha256_before"] = artifact.Sha256,
                ["sha256_after"] = JValue.CreateNull()
            };
        }

        private static void CompleteLedger(JArray ledger)
        {
            foreach (JObject row in ledger.OfType<JObject>())
                row["sha256_after"] = DimensionPlanningHandoffContract.FileSha256(
                    row.Value<string>("path"));
        }

        private static void EnsureLedgerUnchanged(JArray ledger)
        {
            foreach (JObject row in ledger.OfType<JObject>())
                if (!String.Equals(row.Value<string>("sha256_before"),
                        row.Value<string>("sha256_after"), StringComparison.Ordinal))
                    throw new InvalidOperationException(
                        "DIMENSION_HANDOFF_UPSTREAM_CHANGED: " +
                        row.Value<string>("role"));
        }

        private static void EnsureClean(IModelDoc2 model, string label)
        {
            if (model != null && model.GetSaveFlag())
                throw new InvalidOperationException(
                    "DIMENSION_HANDOFF_SOURCE_DIRTY: " + label);
        }

        private static string PersistReference(IModelDocExtension extension,
            object entity)
        {
            if (extension == null || entity == null) return null;
            try
            {
                object raw = extension.GetPersistReference3(entity);
                byte[] bytes = raw as byte[];
                if (bytes == null && raw is Array)
                {
                    var array = (Array)raw;
                    bytes = new byte[array.Length];
                    for (int index = 0; index < array.Length; index++)
                        bytes[index] = Convert.ToByte(array.GetValue(index),
                            CultureInfo.InvariantCulture);
                }
                return bytes != null && bytes.Length > 0
                    ? Convert.ToBase64String(bytes) : null;
            }
            catch { return null; }
        }

        private static string StableToken(string value)
        {
            using (var sha = SHA256.Create())
                return String.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? ""))
                    .Take(8).Select(item => item.ToString("x2",
                        CultureInfo.InvariantCulture)));
        }

        private static double[] ToDoubleArray(object value)
        {
            if (value is double[]) return (double[])value;
            var array = value as Array;
            if (array == null) return new double[0];
            var result = new double[array.Length];
            for (int index = 0; index < array.Length; index++)
                result[index] = Convert.ToDouble(array.GetValue(index),
                    CultureInfo.InvariantCulture);
            return result;
        }

        private static JArray Rounded(IEnumerable<double> values)
        {
            return new JArray(values.Select(Round));
        }

        private static double Round(double value)
        {
            return Math.Round(value, 9, MidpointRounding.AwayFromZero);
        }

        private string ActiveTitle()
        {
            try
            {
                var active = _solidWorks.IActiveDoc2 as IModelDoc2;
                return active != null ? active.GetTitle() : null;
            }
            catch { return null; }
        }

        private void RestoreActive(string title)
        {
            if (String.IsNullOrEmpty(title)) return;
            try
            {
                int errors = 0;
                _solidWorks.ActivateDoc3(title, false, 0, ref errors);
            }
            catch { }
        }

        private void Close(IModelDoc2 model)
        {
            if (model == null) return;
            try { _solidWorks.CloseDoc(model.GetTitle()); } catch { }
        }

        private static bool PathEquals(string first, string second)
        {
            try { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }

        private static void AtomicWriteJson(string path, JToken value)
        {
            string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
            File.WriteAllText(temporary,
                value.ToString(Formatting.Indented) + System.Environment.NewLine,
                new UTF8Encoding(false));
            if (File.Exists(path))
            {
                File.Delete(temporary);
                throw new IOException("refusing to overwrite F1 handoff: " + path);
            }
            File.Move(temporary, path);
        }
    }
}
