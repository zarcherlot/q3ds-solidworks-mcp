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
using SolidworksExecution.Infrastructure;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// F0 research executor. It opens only transaction copies, captures native
    /// display-dimension evidence, saves, closes, reopens read-only, and writes
    /// the evidence report last. It is not a production DimensionPlan executor.
    /// </summary>
    internal sealed class DimensionApiProbeExecutor
    {
        private const string EvidenceProtocol = "solidworks-dimension-api-evidence";
        private readonly ISldWorks _solidWorks;

        public DimensionApiProbeExecutor(ISldWorks solidWorks)
        {
            _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks");
        }

        public JObject Execute(DimensionApiProbeRequest request, JObject sourceRequest)
        {
            if (request == null) throw new ArgumentNullException("request");
            if (sourceRequest == null) throw new ArgumentNullException("sourceRequest");

            string publication = request.PublicationDirectory;
            Directory.CreateDirectory(publication);
            string requestHash = CanonicalSha256(sourceRequest);
            string reportPath = Path.Combine(publication,
                "dimension-api-evidence.json");
            string memoryPath = Path.Combine(publication,
                "dimension-api-readback-memory.json");
            string reopenPath = Path.Combine(publication,
                "dimension-api-readback-reopen.json");
            string requestPath = Path.Combine(publication,
                "dimension-api-probe-request.json");
            AtomicWriteJson(requestPath, sourceRequest);

            var upstream = BuildUpstreamRows(request);
            string revision = SafeRevision();
            JObject baseline = EmptySnapshot("baseline");
            JObject memory = EmptySnapshot("memory");
            JObject reopened = EmptySnapshot("readonly_reopen");
            int insertedCount = 0;
            bool importInvoked = false;
            bool saveSucceeded = false;
            string failure = null;
            JObject nativeProbes = new JObject();
            string transactionDrawing = null;
            string transactionModel = null;
            string originalActiveTitle = ActiveTitle();
            IModelDoc2 drawingModel = null;
            IModelDoc2 transactionModelDocument = null;
            IModelDoc2 reopenedModel = null;

            try
            {
                PrepareTransactionCopy(request, publication,
                    requestHash.Substring(0, 12),
                    out transactionDrawing, out transactionModel);

                int openErrors = 0;
                int openWarnings = 0;
                if (request.SourceKind == "research_model_drawing_pair")
                    drawingModel = CreateResearchTransactionDrawing(
                        request.DrawingTemplate.Path, transactionDrawing);
                else
                    drawingModel = _solidWorks.OpenDoc6(transactionDrawing,
                        (int)swDocumentTypes_e.swDocDRAWING,
                        (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                        "", ref openErrors, ref openWarnings) as IModelDoc2;
                if (drawingModel == null || !(drawingModel is IDrawingDoc))
                    throw new InvalidOperationException(
                        "OpenDoc6 failed for transaction drawing (errors=" +
                        openErrors + ", warnings=" + openWarnings + ")");

                var drawing = (IDrawingDoc)drawingModel;
                if (request.SourceKind == "research_model_drawing_pair")
                {
                    nativeProbes["stable_failures"] =
                        RunStableFailureProbes(drawingModel, drawing);
                    transactionModelDocument = OpenTransactionModel(transactionModel);
                    EnsureResearchModelView(drawingModel, drawing,
                        transactionModelDocument, transactionModel);
                }
                RebuildForReadback(drawingModel);
                baseline = CaptureSnapshot(drawingModel, drawing, "baseline");

                drawingModel.ClearSelection2(true);
                int annotationTypes =
                    (int)swInsertAnnotation_e.swInsertDimensionsMarkedForDrawing |
                    (int)swInsertAnnotation_e.swInsertDimensionsNotMarkedForDrawing |
                    (int)swInsertAnnotation_e.swInsertHoleWizardProfileDimensions |
                    (int)swInsertAnnotation_e.swInsertHoleWizardLocationDimensions |
                    (int)swInsertAnnotation_e.swInsertholeCallout |
                    (int)swInsertAnnotation_e.swInsertTolerancedDims;
                importInvoked = true;
                object inserted = drawing.InsertModelAnnotations3(
                    (int)swImportModelItemsSource_e.swImportModelItemsFromEntireModel,
                    annotationTypes, true, true, false, true);
                insertedCount = CountArray(inserted);
                if (request.SourceKind == "research_model_drawing_pair")
                {
                    JObject explicitProbes = RunExplicitCapabilityProbes(
                        drawingModel, drawing);
                    foreach (JProperty property in explicitProbes.Properties())
                        nativeProbes[property.Name] = property.Value;
                }
                RebuildForReadback(drawingModel);
                memory = CaptureSnapshot(drawingModel, drawing, "memory");
                memory["insert_model_annotations_invoked"] = true;
                memory["inserted_annotation_count"] = insertedCount;
                memory["explicit_native_probes"] = nativeProbes;

                if (transactionModelDocument != null)
                {
                    int modelSaveErrors = 0;
                    int modelSaveWarnings = 0;
                    bool modelSaved = transactionModelDocument.Save3(
                        (int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                        ref modelSaveErrors, ref modelSaveWarnings);
                    memory["transaction_model_save_errors"] = modelSaveErrors;
                    memory["transaction_model_save_warnings"] = modelSaveWarnings;
                    if (!modelSaved || modelSaveErrors != 0)
                        throw new InvalidOperationException(
                            "Save3 failed for transaction model (errors=" +
                            modelSaveErrors + ", warnings=" + modelSaveWarnings + ")");
                }

                int saveErrors = 0;
                int saveWarnings = 0;
                drawingModel.ClearSelection2(true);
                saveSucceeded = drawingModel.Save3(
                    (int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                    ref saveErrors, ref saveWarnings);
                memory["save_errors"] = saveErrors;
                memory["save_warnings"] = saveWarnings;
                if (!saveSucceeded || saveErrors != 0 ||
                    !File.Exists(transactionDrawing))
                    throw new InvalidOperationException(
                        "Save3 failed for transaction drawing (errors=" +
                        saveErrors + ", warnings=" + saveWarnings + ")");

                string drawingTitle = drawingModel.GetTitle();
                _solidWorks.CloseDoc(drawingTitle);
                drawingModel = null;
                CloseProbeDocument(transactionModelDocument);
                transactionModelDocument = null;

                openErrors = 0;
                openWarnings = 0;
                reopenedModel = _solidWorks.OpenDoc6(transactionDrawing,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    "", ref openErrors, ref openWarnings) as IModelDoc2;
                if (reopenedModel == null || !(reopenedModel is IDrawingDoc))
                    throw new InvalidOperationException(
                        "read-only reopen failed (errors=" + openErrors +
                        ", warnings=" + openWarnings + ")");
                RebuildForReadback(reopenedModel);
                reopened = CaptureSnapshot(reopenedModel,
                    (IDrawingDoc)reopenedModel, "readonly_reopen");
                VerifyOriginalPersistReferences(reopenedModel, memory, reopened);
            }
            catch (Exception exception)
            {
                failure = exception.GetType().Name + ": " + exception.Message;
                ExecLog.Write("dimension-probe: " + failure);
            }
            finally
            {
                CloseProbeDocument(reopenedModel);
                CloseProbeDocument(drawingModel);
                CloseProbeDocument(transactionModelDocument);
                if (!string.IsNullOrEmpty(transactionModel))
                    CloseExactPathIfOpen(transactionModel);
                RestoreActiveDocument(originalActiveTitle);
                CompleteUpstreamRows(upstream);
            }

            AtomicWriteJson(memoryPath, memory);
            AtomicWriteJson(reopenPath, reopened);
            JArray capabilities = BuildCapabilityRows(baseline, memory, reopened,
                importInvoked, insertedCount, saveSucceeded, failure,
                memoryPath, reopenPath, nativeProbes);
            JObject report = new JObject
            {
                ["protocol_id"] = EvidenceProtocol,
                ["schema_version"] = "1.0",
                ["probe_id"] = "DPE-" + requestHash.Substring(0, 16),
                ["created_at"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                ["execution_mode"] = "live",
                ["source_kind"] = request.SourceKind,
                ["solidworks"] = SolidWorksIdentity(revision),
                ["source_request_sha256"] = requestHash,
                ["upstream_immutability"] = upstream,
                ["capabilities"] = capabilities
            };
            AtomicWriteJson(reportPath, report);
            string reportHash = FileSha256(reportPath);

            return new JObject
            {
                ["status"] = failure == null ? "evidence_ready" : "capability_blocked",
                ["report_path"] = reportPath,
                ["report_sha256"] = reportHash,
                ["publication_directory"] = publication,
                ["transaction_drawing"] = transactionDrawing,
                ["inserted_annotation_count"] = insertedCount,
                ["failure"] = failure == null ? JValue.CreateNull() :
                    (JToken)failure
            };
        }

        private static void PrepareTransactionCopy(DimensionApiProbeRequest request,
            string publication, string transactionId, out string drawingPath,
            out string modelPath)
        {
            string transaction = Path.Combine(publication, "transaction");
            Directory.CreateDirectory(transaction);
            string baseName = "F0-" + transactionId;
            modelPath = null;
            if (request.SourceKind == "research_model_drawing_pair")
            {
                modelPath = Path.Combine(transaction, baseName + ".SLDPRT");
                drawingPath = Path.Combine(transaction, baseName + ".SLDDRW");
                File.Copy(request.SourceModel.Path, modelPath, false);
                File.Copy(request.SourceDrawing.Path,
                    Path.Combine(transaction, baseName + "-source.SLDDRW"), false);
            }
            else
            {
                drawingPath = Path.Combine(transaction, baseName + ".SLDDRW");
                File.Copy(request.VerifiedDrawing.Path, drawingPath, false);
            }
        }

        private IModelDoc2 CreateResearchTransactionDrawing(string templatePath,
            string drawingPath)
        {
            if (File.Exists(drawingPath))
                throw new IOException(
                    "refusing to overwrite F0 transaction drawing: " + drawingPath);
            var drawing = _solidWorks.NewDocument(templatePath, 0, 0.0, 0.0)
                as IModelDoc2;
            if (drawing == null || !(drawing is IDrawingDoc))
                throw new InvalidOperationException(
                    "NewDocument failed for the hash-bound drawing template.");
            int errors = 0;
            int warnings = 0;
            drawing.ClearSelection2(true);
            bool saved = drawing.Extension.SaveAs3(drawingPath,
                (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                (int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                null, null, ref errors, ref warnings);
            if (!saved || errors != 0 || !File.Exists(drawingPath))
            {
                CloseProbeDocument(drawing);
                throw new InvalidOperationException(
                    "SaveAs3 failed for the new research transaction drawing (errors=" +
                    errors + ", warnings=" + warnings + ")");
            }
            return drawing;
        }

        private IModelDoc2 OpenTransactionModel(string modelPath)
        {
            int errors = 0;
            int warnings = 0;
            var model = _solidWorks.OpenDoc6(modelPath,
                (int)swDocumentTypes_e.swDocPART,
                (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                "", ref errors, ref warnings) as IModelDoc2;
            if (model == null)
                throw new InvalidOperationException(
                    "OpenDoc6 failed for transaction model (errors=" + errors +
                    ", warnings=" + warnings + ")");
            return model;
        }

        private static void EnsureResearchModelView(IModelDoc2 drawingModel,
            IDrawingDoc drawing, IModelDoc2 transactionModel, string modelPath)
        {
            var sheetView = drawing.GetFirstView() as IView;
            if (sheetView != null && sheetView.GetNextView() is IView)
                return;

            Array names = transactionModel.GetModelViewNames() as Array;
            if (names == null || names.Length < 10)
                throw new InvalidOperationException(
                    "GetModelViewNames did not return the normal view plus nine standard views.");
            double width = 0.42;
            double height = 0.297;
            var sheet = drawing.GetCurrentSheet() as ISheet;
            var properties = sheet != null ? sheet.GetProperties2() as double[] : null;
            if (properties != null && properties.Length >= 7 &&
                properties[5] > 0.0 && properties[6] > 0.0)
            {
                width = properties[5];
                height = properties[6];
            }

            var specifications = new[]
            {
                new { Index = 1, X = 0.25, Y = 0.67 },
                new { Index = 4, X = 0.68, Y = 0.67 },
                new { Index = 5, X = 0.25, Y = 0.27 },
                new { Index = 7, X = 0.68, Y = 0.27 }
            };
            foreach (var specification in specifications)
            {
                string modelView = Convert.ToString(
                    names.GetValue(specification.Index),
                    CultureInfo.InvariantCulture);
                if (string.IsNullOrWhiteSpace(modelView))
                    throw new InvalidOperationException(
                        "The transaction model does not expose standard view index " +
                        specification.Index + ".");
                var created = drawing.CreateDrawViewFromModelView3(modelPath,
                    modelView, width * specification.X,
                    height * specification.Y, 0.0) as IView;
                if (created == null)
                    throw new InvalidOperationException(
                        "CreateDrawViewFromModelView3 returned null for standard view index " +
                        specification.Index + ".");
            }
            drawingModel.SetSaveFlag();
            RebuildForReadback(drawingModel);
        }

        private JObject CaptureSnapshot(IModelDoc2 model, IDrawingDoc drawing,
            string phase)
        {
            var records = new JArray();
            var viewReadback = new JArray();
            var duplicateKeys = new Dictionary<string, int>(
                StringComparer.OrdinalIgnoreCase);
            int viewCount = 0;
            int expectedCount = 0;
            int traversedCount = 0;
            bool complete = true;
            object viewObject = drawing.GetFirstView();
            int viewGuard = 0;
            while (viewObject != null && viewGuard++ < 2000)
            {
                var view = viewObject as IView;
                object nextView = view != null ? view.GetNextView() : null;
                if (view != null)
                {
                    viewCount++;
                    JArray viewErrors;
                    IList<JObject> viewDimensions = ReadViewDimensions(view,
                        out viewErrors);
                    if (viewErrors.Count > 0) complete = false;
                    viewReadback.Add(new JObject
                    {
                        ["view"] = view.Name ?? "",
                        ["dimension_count"] = viewDimensions.Count,
                        ["records"] = new JArray(viewDimensions.Select(item =>
                            item.DeepClone())),
                        ["read_errors"] = viewErrors
                    });
                    int expected = -1;
                    try { expected = view.GetDimensionCount4(); }
                    catch { complete = false; }
                    if (expected >= 0) expectedCount += expected;
                    if (expected >= 0 && expected != viewDimensions.Count)
                        complete = false;

                    object displayObject = null;
                    try { displayObject = view.GetFirstDisplayDimension5(); }
                    catch { complete = false; }
                    int dimensionGuard = 0;
                    int viewTraversed = 0;
                    while (displayObject != null && dimensionGuard++ < 2000)
                    {
                        var display = displayObject as IDisplayDimension;
                        object nextDisplay = null;
                        if (display != null)
                        {
                            JObject record = ReadDimensionRecord(model, view,
                                display, duplicateKeys,
                                viewTraversed < viewDimensions.Count
                                    ? viewDimensions[viewTraversed] : null);
                            records.Add(record);
                            traversedCount++;
                            viewTraversed++;
                            try { nextDisplay = display.GetNext5(); }
                            catch { complete = false; }
                        }
                        else
                        {
                            complete = false;
                        }
                        displayObject = nextDisplay;
                    }
                    if (dimensionGuard >= 2000) complete = false;
                    if (expected >= 0 && expected != viewTraversed) complete = false;
                }
                viewObject = nextView;
            }
            if (viewGuard >= 2000) complete = false;
            return new JObject
            {
                ["phase"] = phase,
                ["view_count"] = viewCount,
                ["expected_dimension_count"] = expectedCount,
                ["traversed_dimension_count"] = traversedCount,
                ["iteration_complete"] = complete,
                ["records"] = records,
                ["view_dimension_readback"] = viewReadback
            };
        }

        private JObject ReadDimensionRecord(IModelDoc2 model, IView view,
            IDisplayDimension display, IDictionary<string, int> duplicateKeys,
            JObject viewDimension)
        {
            var errors = new JArray();
            IDimension dimension = null;
            IAnnotation annotation = null;
            int type = 0;
            double value = double.NaN;
            double directValue = double.NaN;
            string fullName = "";
            string selectionName = "";
            bool holeCallout = false;
            try { dimension = display.GetDimension2(0) as IDimension; }
            catch (Exception ex) { errors.Add("dimension: " + ex.Message); }
            try { annotation = display.GetAnnotation() as IAnnotation; }
            catch (Exception ex) { errors.Add("annotation: " + ex.Message); }
            try { type = display.Type2; }
            catch (Exception ex) { errors.Add("type: " + ex.Message); }
            try { holeCallout = display.IsHoleCallout(); }
            catch (Exception ex) { errors.Add("hole_callout: " + ex.Message); }
            try { selectionName = display.GetNameForSelection() ?? ""; }
            catch (Exception ex) { errors.Add("selection_name: " + ex.Message); }
            if (dimension != null)
            {
                try { fullName = dimension.FullName ?? ""; }
                catch (Exception ex) { errors.Add("full_name: " + ex.Message); }
                try
                {
                    double[] values = ToDoubleArray(
                        dimension.GetSystemValue3(1, null));
                    if (values.Length > 0) directValue = values[0];
                }
                catch (Exception ex) { errors.Add("value: " + ex.Message); }
                if (double.IsNaN(directValue) || double.IsInfinity(directValue))
                {
                    try { directValue = dimension.Value; }
                    catch (Exception ex) { errors.Add("value_fallback: " + ex.Message); }
                }
            }
            double? viewValue = viewDimension != null
                ? viewDimension.Value<double?>("value_si") : null;
            value = viewValue.HasValue ? viewValue.Value : directValue;

            string viewName = "";
            try { viewName = view.Name ?? ""; } catch { }
            JArray position = ReadPosition(annotation, errors);
            JObject text = ReadText(display, errors);
            JObject displayStrings = viewDimension != null &&
                viewDimension["display_strings"] is JObject
                ? (JObject)viewDimension["display_strings"].DeepClone()
                : new JObject();
            string dimensionId = viewDimension != null
                ? viewDimension.Value<string>("dimension_id") ?? "" : "";
            string baseKey = !string.IsNullOrEmpty(dimensionId)
                ? viewName + "|" + dimensionId
                : viewName + "|" + type + "|" + holeCallout + "|" +
                    position.ToString(Formatting.None) + "|" +
                    (text.Value<string>("all") ?? "");
            int duplicate;
            if (!duplicateKeys.TryGetValue(baseKey, out duplicate)) duplicate = 0;
            duplicateKeys[baseKey] = duplicate + 1;
            string key = baseKey + "#" + duplicate;

            JObject tolerance = ReadTolerance(dimension, errors);
            JObject attachments = ReadAttachments(model, view, annotation, errors);
            JObject displayData = ReadDisplayData(annotation, errors);
            JArray holeVariables = new JArray();
            if (holeCallout)
            {
                try { holeVariables = ToJsonArray(display.GetHoleCalloutVariables()); }
                catch (Exception ex) { errors.Add("hole_variables: " + ex.Message); }
            }

            JObject identity = new JObject
            {
                ["key"] = key,
                ["dimension_id"] = dimensionId,
                ["type"] = type,
                ["value_si"] = JNumber(value),
                ["position_sheet_m"] = position,
                ["display_strings"] = displayStrings,
                ["attachment_types"] = attachments["types"].DeepClone(),
                ["hole_callout_variables"] = holeVariables.DeepClone()
            };
            return new JObject
            {
                ["key"] = key,
                ["view"] = viewName,
                ["dimension_id"] = dimensionId,
                ["selection_name"] = selectionName,
                ["full_name"] = fullName,
                ["type"] = type,
                ["value_si"] = JNumber(value),
                ["direct_dimension_value_si"] = JNumber(directValue),
                ["view_dimension_value_si"] = viewValue.HasValue
                    ? JNumber(viewValue.Value) : JValue.CreateNull(),
                ["is_hole_callout"] = holeCallout,
                ["position_sheet_m"] = position,
                ["text"] = text,
                ["display_strings"] = displayStrings,
                ["tolerance"] = tolerance,
                ["attachments"] = attachments,
                ["display_data"] = displayData,
                ["hole_callout_variables"] = holeVariables,
                ["identity_contract"] = identity,
                ["identity_sha256"] = Sha256Text(identity.ToString(Formatting.None)),
                ["read_errors"] = errors
            };
        }

        private static IList<JObject> ReadViewDimensions(IView view,
            out JArray errors)
        {
            errors = new JArray();
            var result = new List<JObject>();
            try
            {
                if (view.GetDimensionCount4() == 0) return result;
            }
            catch (Exception ex)
            {
                errors.Add("dimension_count: " + ex.Message);
                return result;
            }
            string[] ids;
            double[] info;
            try
            {
                // SOLIDWORKS documents that GetDimensionIds4 and
                // GetDimensionInfo7 are index-aligned when called consecutively.
                ids = ToStringArray(view.GetDimensionIds4());
                info = ToDoubleArray(view.GetDimensionInfo7());
            }
            catch (Exception ex)
            {
                errors.Add("dimension_ids_info: " + ex.Message);
                return result;
            }

            int stringSize = 0;
            string[] displayStrings = new string[0];
            try
            {
                displayStrings = ToStringArray(
                    view.GetDimensionDisplayString5(out stringSize));
            }
            catch (Exception ex)
            {
                errors.Add("dimension_display_strings: " + ex.Message);
            }

            if (info.Length == 0)
            {
                if (ids.Length != 0)
                    errors.Add("GetDimensionInfo7 returned no count for non-empty IDs");
                return result;
            }
            int count = Convert.ToInt32(info[0], CultureInfo.InvariantCulture);
            if (count < 0 || count != ids.Length)
            {
                errors.Add("dimension aggregate count mismatch: info=" + count +
                    ", ids=" + ids.Length);
                return result;
            }
            if (stringSize < 0 || (stringSize > 0 &&
                displayStrings.Length != count * stringSize))
                errors.Add("dimension display string count mismatch: strings=" +
                    displayStrings.Length + ", count=" + count +
                    ", size=" + stringSize);

            int cursor = 1;
            for (int index = 0; index < count; index++)
            {
                if (cursor >= info.Length)
                {
                    errors.Add("GetDimensionInfo7 ended before dimension " + index);
                    break;
                }
                int aggregateType = Convert.ToInt32(info[cursor],
                    CultureInfo.InvariantCulture);
                // GetDimensionInfo7 returns a fixed 52-double record for every
                // dimension. Angular and angular-ordinate slots remain reserved
                // for the other dimension kinds.
                const int recordSize = 52;
                const int valueOffset = 47;
                if (cursor + recordSize > info.Length)
                {
                    errors.Add("GetDimensionInfo7 record " + index +
                        " is truncated");
                    break;
                }

                var strings = new JObject();
                string[] names =
                {
                    "primary_value", "primary_tolerance_max",
                    "primary_tolerance_min", "dual_value",
                    "dual_tolerance_max", "dual_tolerance_min", "prefix",
                    "suffix", "callout_above", "callout_below", "bottom"
                };
                for (int part = 0; part < names.Length; part++)
                {
                    int stringIndex = index * stringSize + part;
                    strings[names[part]] = stringSize > part &&
                        stringIndex < displayStrings.Length
                        ? displayStrings[stringIndex] ?? "" : "";
                }
                result.Add(new JObject
                {
                    ["dimension_id"] = ids[index] ?? "",
                    ["aggregate_type"] = aggregateType,
                    ["value_si"] = JNumber(info[cursor + valueOffset]),
                    ["display_strings"] = strings
                });
                cursor += recordSize;
            }
            if (cursor != info.Length)
                errors.Add("GetDimensionInfo7 trailing value count=" +
                    (info.Length - cursor));
            return result;
        }

        private JObject RunExplicitCapabilityProbes(IModelDoc2 drawingModel,
            IDrawingDoc drawing)
        {
            var result = new JObject();
            IView sheet = drawing.GetFirstView() as IView;
            var views = new List<IView>();
            IView cursor = sheet != null ? sheet.GetNextView() as IView : null;
            while (cursor != null && views.Count < 32)
            {
                views.Add(cursor);
                cursor = cursor.GetNextView() as IView;
            }
            if (views.Count == 0)
            {
                result["failure"] = "research drawing has no model view";
                return result;
            }

            var circles = new List<Tuple<IEdge, double[]>>();
            var lines = new List<Tuple<IEdge, double[], double[]>>();
            IView circleView = views[0];
            IView lineView = views[0];
            foreach (IView candidateView in views)
            {
                var candidateCircles = new List<Tuple<IEdge, double[]>>();
                var candidateLines = new List<Tuple<IEdge, double[], double[]>>();
                foreach (IEdge edge in VisibleEdges(candidateView))
                {
                    ICurve curve = null;
                    try { curve = edge.GetCurve() as ICurve; } catch { }
                    double[] center;
                    if (curve != null && curve.IsCircle() &&
                        TryProjectCircleCenter(candidateView, curve, out center))
                    {
                        candidateCircles.Add(Tuple.Create(edge, center));
                        continue;
                    }
                    double[] first;
                    double[] second;
                    if (curve != null && curve.IsLine() &&
                        TryProjectLine(candidateView, edge, out first, out second))
                        candidateLines.Add(Tuple.Create(edge, first, second));
                }
                if (candidateCircles.Count > circles.Count)
                {
                    circles = candidateCircles;
                    circleView = candidateView;
                }
                if (candidateLines.Count > lines.Count)
                {
                    lines = candidateLines;
                    lineView = candidateView;
                }
            }

            result["model_view_count"] = views.Count;
            result["visible_circle_count"] = circles.Count;
            result["visible_line_count"] = lines.Count;
            result["radius_dimension"] = TryAddCircularDimension(drawingModel,
                circleView, circles, false);
            result["diameter_dimension"] = TryAddCircularDimension(drawingModel,
                circleView, circles, true);
            result["hole_callout"] = TryAddHoleCallout(drawingModel, drawing,
                circleView, circles);
            result["chamfer_dimension"] = TryAddChamferDimension(drawingModel,
                drawing, lineView, lines);
            IView formatView = views.FirstOrDefault(item =>
            {
                try { return item.GetDimensionCount4() > 0; }
                catch { return false; }
            }) ?? views[0];
            JObject formatting = TrySetDimensionFormatting(formatView);
            result["dimension_tolerance"] = formatting["dimension_tolerance"];
            result["dimension_prefix_suffix"] =
                formatting["dimension_prefix_suffix"];
            drawingModel.ClearSelection2(true);
            return result;
        }

        private static JObject RunStableFailureProbes(IModelDoc2 model,
            IDrawingDoc drawing)
        {
            var result = new JObject();
            model.ClearSelection2(true);
            result["empty_display_dimension_count"] =
                (drawing.GetFirstView() as IView)?.GetDimensionCount4() ?? 0;
            result["linear_dimension"] = ExpectedNullProbe(
                "IModelDoc2.AddDimension2", () =>
                    model.AddDimension2(0.21, 0.1485, 0.0));
            result["radius_dimension"] = ExpectedNullProbe(
                "IModelDoc2.AddRadialDimension2", () =>
                    model.AddRadialDimension2(0.21, 0.1485, 0.0));
            result["diameter_dimension"] = ExpectedNullProbe(
                "IModelDoc2.AddDiameterDimension2", () =>
                    model.AddDiameterDimension2(0.21, 0.1485, 0.0));
            result["hole_callout"] = ExpectedNullProbe(
                "IDrawingDoc.AddHoleCallout2", () =>
                    drawing.AddHoleCallout2(0.21, 0.1485, 0.0));
            result["chamfer_dimension"] = ExpectedNullProbe(
                "IDrawingDoc.AddChamferDim", () =>
                    drawing.AddChamferDim(0.21, 0.1485, 0.0));
            model.ClearSelection2(true);
            return result;
        }

        private static JObject ExpectedNullProbe(string api, Func<object> action)
        {
            var row = ProbeRow(api);
            row["attempt_count"] = 1;
            try
            {
                object value = action();
                row["expected_failure_observed"] = value == null;
                row["unexpected_result_type"] = value == null
                    ? JValue.CreateNull() : (JToken)value.GetType().FullName;
            }
            catch (Exception ex)
            {
                row["expected_failure_observed"] = true;
                row["exception_type"] = ex.GetType().FullName;
                row["last_error"] = ex.Message;
            }
            return row;
        }

        private JObject TryAddCircularDimension(IModelDoc2 model, IView view,
            IList<Tuple<IEdge, double[]>> circles, bool diameter)
        {
            var row = ProbeRow(diameter ? "IModelDoc2.AddDiameterDimension2" :
                "IModelDoc2.AddRadialDimension2");
            foreach (Tuple<IEdge, double[]> circle in circles.Take(32))
            {
                row["attempt_count"] = row.Value<int>("attempt_count") + 1;
                try
                {
                    model.ClearSelection2(true);
                    if (!view.SelectEntity(circle.Item1, false)) continue;
                    double[] point = circle.Item2;
                    var display = (diameter
                        ? model.AddDiameterDimension2(point[0] + 0.012,
                            point[1] + 0.012, 0.0)
                        : model.AddRadialDimension2(point[0] - 0.012,
                            point[1] + 0.012, 0.0)) as IDisplayDimension;
                    if (display == null) continue;
                    row["success"] = true;
                    row["result_type"] = display.Type2;
                    row["selection_name"] = display.GetNameForSelection() ?? "";
                    break;
                }
                catch (Exception ex) { row["last_error"] = ex.Message; }
            }
            model.ClearSelection2(true);
            return row;
        }

        private static JObject TryAddHoleCallout(IModelDoc2 model,
            IDrawingDoc drawing, IView view,
            IList<Tuple<IEdge, double[]>> circles)
        {
            var row = ProbeRow("IDrawingDoc.AddHoleCallout2");
            foreach (Tuple<IEdge, double[]> circle in circles.Take(32))
            {
                row["attempt_count"] = row.Value<int>("attempt_count") + 1;
                try
                {
                    model.ClearSelection2(true);
                    if (!view.SelectEntity(circle.Item1, false)) continue;
                    double[] point = circle.Item2;
                    var display = drawing.AddHoleCallout2(point[0] + 0.018,
                        point[1] - 0.018, 0.0) as IDisplayDimension;
                    if (display == null) continue;
                    row["success"] = true;
                    row["result_type"] = display.Type2;
                    row["selection_name"] = display.GetNameForSelection() ?? "";
                    break;
                }
                catch (Exception ex) { row["last_error"] = ex.Message; }
            }
            model.ClearSelection2(true);
            return row;
        }

        private static JObject TryAddChamferDimension(IModelDoc2 model,
            IDrawingDoc drawing, IView view,
            IList<Tuple<IEdge, double[], double[]>> lines)
        {
            var row = ProbeRow("IDrawingDoc.AddChamferDim");
            int attempts = 0;
            for (int first = 0; first < lines.Count && attempts < 64; first++)
            for (int second = first + 1; second < lines.Count && attempts < 64;
                second++)
            {
                double[] joint;
                if (!TrySharedPoint(lines[first], lines[second], out joint))
                    continue;
                attempts++;
                row["attempt_count"] = attempts;
                try
                {
                    model.ClearSelection2(true);
                    if (!view.SelectEntity(lines[first].Item1, false) ||
                        !view.SelectEntity(lines[second].Item1, true)) continue;
                    var display = drawing.AddChamferDim(joint[0] + 0.015,
                        joint[1] + 0.015, 0.0) as IDisplayDimension;
                    if (display == null) continue;
                    row["success"] = true;
                    row["result_type"] = display.Type2;
                    row["selection_name"] = display.GetNameForSelection() ?? "";
                    model.ClearSelection2(true);
                    return row;
                }
                catch (Exception ex) { row["last_error"] = ex.Message; }
            }
            model.ClearSelection2(true);
            return row;
        }

        private static JObject TrySetDimensionFormatting(IView view)
        {
            var tolerance = ProbeRow("IDimensionTolerance.SetValues");
            var text = ProbeRow("IDisplayDimension.SetText");
            object current = null;
            try { current = view.GetFirstDisplayDimension5(); }
            catch (Exception ex)
            {
                tolerance["last_error"] = ex.Message;
                text["last_error"] = ex.Message;
            }
            int guard = 0;
            while (current != null && guard++ < 2000)
            {
                var display = current as IDisplayDimension;
                object next = display != null ? display.GetNext5() : null;
                if (display != null && new[] { 2, 11, 12 }.Contains(display.Type2))
                {
                    try
                    {
                        text["attempt_count"] = 1;
                        display.SetText((int)swDimensionTextParts_e.swDimensionTextPrefix,
                            "F0-PREFIX ");
                        display.SetText((int)swDimensionTextParts_e.swDimensionTextSuffix,
                            " F0-SUFFIX");
                        text["success"] =
                            (display.GetText((int)swDimensionTextParts_e.
                                swDimensionTextPrefix) ?? "").Contains("F0-PREFIX") &&
                            (display.GetText((int)swDimensionTextParts_e.
                                swDimensionTextSuffix) ?? "").Contains("F0-SUFFIX");
                    }
                    catch (Exception ex) { text["last_error"] = ex.Message; }
                    try
                    {
                        tolerance["attempt_count"] = 1;
                        var dimension = display.GetDimension2(0) as IDimension;
                        var value = dimension != null
                            ? dimension.Tolerance as IDimensionTolerance : null;
                        if (value != null)
                        {
                            value.Type = (int)swTolType_e.swTolBILAT;
                            tolerance["success"] = value.SetValues(-0.0001,
                                0.0002);
                        }
                    }
                    catch (Exception ex) { tolerance["last_error"] = ex.Message; }
                    break;
                }
                current = next;
            }
            return new JObject
            {
                ["dimension_tolerance"] = tolerance,
                ["dimension_prefix_suffix"] = text
            };
        }

        private static JObject ProbeRow(string api)
        {
            return new JObject
            {
                ["api"] = api,
                ["attempt_count"] = 0,
                ["success"] = false
            };
        }

        private static IEnumerable<IEdge> VisibleEdges(IView view)
        {
            var result = new List<IEdge>();
            var components = view.GetVisibleComponents() as Array;
            if (components == null) return result;
            foreach (object componentObject in components)
            {
                var component = componentObject as Component2;
                var entities = component != null
                    ? view.GetVisibleEntities2(component, 1) as Array : null;
                if (entities == null) continue;
                foreach (object entity in entities)
                    if (entity is IEdge) result.Add((IEdge)entity);
            }
            return result;
        }

        private bool TryProjectCircleCenter(IView view, ICurve curve,
            out double[] center)
        {
            center = null;
            double[] circle = curve.CircleParams as double[];
            return circle != null && circle.Length >= 3 &&
                TryProjectPoint(view, circle.Take(3).ToArray(), out center);
        }

        private bool TryProjectLine(IView view, IEdge edge, out double[] first,
            out double[] second)
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
                    sheetPoint.All(value => !double.IsNaN(value) &&
                        !double.IsInfinity(value));
            }
            catch { return false; }
        }

        private static bool TrySharedPoint(Tuple<IEdge, double[], double[]> first,
            Tuple<IEdge, double[], double[]> second, out double[] point)
        {
            point = null;
            foreach (double[] a in new[] { first.Item2, first.Item3 })
            foreach (double[] b in new[] { second.Item2, second.Item3 })
            {
                double dx = a[0] - b[0];
                double dy = a[1] - b[1];
                if (dx * dx + dy * dy > 1e-12) continue;
                point = new[] { (a[0] + b[0]) * 0.5,
                    (a[1] + b[1]) * 0.5 };
                return true;
            }
            return false;
        }

        private static JArray ReadPosition(IAnnotation annotation, JArray errors)
        {
            if (annotation == null) return new JArray();
            try { return ToRoundedArray(annotation.GetPosition()); }
            catch (Exception ex)
            {
                errors.Add("position: " + ex.Message);
                return new JArray();
            }
        }

        private static JObject ReadText(IDisplayDimension display, JArray errors)
        {
            var result = new JObject();
            foreach (var part in new[]
            {
                new { Name = "all", Value = 0 },
                new { Name = "prefix", Value = 1 },
                new { Name = "suffix", Value = 2 },
                new { Name = "callout_above", Value = 3 },
                new { Name = "callout_below", Value = 4 }
            })
            {
                try { result[part.Name] = display.GetText(part.Value) ?? ""; }
                catch (Exception ex)
                {
                    result[part.Name] = "";
                    errors.Add("text_" + part.Name + ": " + ex.Message);
                }
            }
            return result;
        }

        private static JObject ReadTolerance(IDimension dimension, JArray errors)
        {
            var result = new JObject
            {
                ["available"] = false,
                ["type"] = 0,
                ["minimum"] = 0.0,
                ["maximum"] = 0.0,
                ["minimum_valid"] = false,
                ["maximum_valid"] = false
            };
            if (dimension == null) return result;
            try
            {
                var tolerance = dimension.Tolerance as IDimensionTolerance;
                if (tolerance == null) return result;
                double minimum;
                double maximum;
                int minimumState = tolerance.GetMinValue2(out minimum);
                int maximumState = tolerance.GetMaxValue2(out maximum);
                result["available"] = true;
                result["type"] = tolerance.Type;
                result["minimum"] = Round(minimum);
                result["maximum"] = Round(maximum);
                result["minimum_valid"] = minimumState == 0;
                result["maximum_valid"] = maximumState == 0;
            }
            catch (Exception ex) { errors.Add("tolerance: " + ex.Message); }
            return result;
        }

        private static JObject ReadAttachments(IModelDoc2 model, IView view,
            IAnnotation annotation, JArray errors)
        {
            var typesJson = new JArray();
            var refsJson = new JArray();
            var resolvedJson = new JArray();
            var modelRefsJson = new JArray();
            var modelResolvedJson = new JArray();
            var skippedSlots = new JArray();
            int count = 0;
            IModelDoc2 referencedModel = null;
            try { referencedModel = view.ReferencedDocument as IModelDoc2; }
            catch { }
            if (annotation != null)
            {
                try
                {
                    Array entities = annotation.GetAttachedEntities3() as Array;
                    Array types = annotation.GetAttachedEntityTypes() as Array;
                    int rawCount = entities != null ? entities.Length : 0;
                    for (int index = 0; index < rawCount; index++)
                    {
                        object entity = entities.GetValue(index);
                        int type = types != null && index < types.Length
                            ? Convert.ToInt32(types.GetValue(index),
                                CultureInfo.InvariantCulture) : 0;
                        if (entity == null || type == 0)
                        {
                            skippedSlots.Add(new JObject
                            {
                                ["index"] = index,
                                ["type"] = type,
                                ["entity_is_null"] = entity == null,
                                ["reason"] = entity == null
                                    ? "null attachment placeholder"
                                    : "unknown attachment type"
                            });
                            continue;
                        }
                        count++;
                        typesJson.Add(type);
                        string persist = PersistReference(model.Extension, entity);
                        refsJson.Add(persist == null ? JValue.CreateNull() :
                            (JToken)persist);
                        int state;
                        object resolved = ResolveReference(model.Extension,
                            persist, out state);
                        resolvedJson.Add(resolved != null && state == 0);
                        string modelPersist = referencedModel != null
                            ? PersistReference(referencedModel.Extension, entity)
                            : null;
                        modelRefsJson.Add(modelPersist == null
                            ? JValue.CreateNull() : (JToken)modelPersist);
                        int modelState = 1;
                        object modelResolved = referencedModel != null
                            ? ResolveReference(referencedModel.Extension,
                                modelPersist, out modelState) : null;
                        modelResolvedJson.Add(modelResolved != null &&
                            modelState == 0);
                    }
                }
                catch (Exception ex) { errors.Add("attachments: " + ex.Message); }
            }
            return new JObject
            {
                ["count"] = count,
                ["raw_slot_count"] = count + skippedSlots.Count,
                ["skipped_slots"] = skippedSlots,
                ["types"] = typesJson,
                ["persistent_references"] = refsJson,
                ["resolved_in_session"] = resolvedJson,
                ["referenced_model_persistent_references"] = modelRefsJson,
                ["referenced_model_resolved_in_session"] = modelResolvedJson
            };
        }

        private static JObject ReadDisplayData(IAnnotation annotation, JArray errors)
        {
            var result = new JObject
            {
                ["available"] = false,
                ["text"] = new JArray(),
                ["lines"] = new JArray(),
                ["arcs"] = new JArray(),
                ["arrow_heads"] = new JArray(),
                ["triangles"] = new JArray(),
                ["text_bounds_exact"] = false
            };
            if (annotation == null) return result;
            try
            {
                var data = annotation.GetDisplayData() as IDisplayData;
                if (data == null) return result;
                result["available"] = true;
                var text = (JArray)result["text"];
                int textCount = data.GetTextCount();
                for (int index = 0; index < textCount; index++)
                {
                    text.Add(new JObject
                    {
                        ["value"] = data.GetTextAtIndex(index) ?? "",
                        ["position_sheet_m"] =
                            ToRoundedArray(data.GetTextPositionAtIndex(index)),
                        ["height_m"] = Round(data.GetTextHeightAtIndex(index)),
                        ["angle_rad"] = Round(data.GetTextAngleAtIndex(index)),
                        ["font"] = data.GetTextFontAtIndex(index) ?? ""
                    });
                }
                ReadDisplayPrimitives((JArray)result["lines"],
                    data.GetLineCount(), index => data.GetLineAtIndex3(index));
                ReadDisplayPrimitives((JArray)result["arcs"],
                    data.GetArcCount(), index => data.GetArcAtIndex2(index));
                ReadDisplayPrimitives((JArray)result["arrow_heads"],
                    data.GetArrowHeadCount(),
                    index => data.GetArrowHeadAtIndex2(index));
                ReadDisplayPrimitives((JArray)result["triangles"],
                    data.GetTriangleCount(), index => data.GetTriangleAtIndex(index));
            }
            catch (Exception ex) { errors.Add("display_data: " + ex.Message); }
            return result;
        }

        private static void ReadDisplayPrimitives(JArray destination, int count,
            Func<int, object> reader)
        {
            for (int index = 0; index < count; index++)
            {
                try { destination.Add(ToRoundedArray(reader(index))); }
                catch (Exception ex)
                {
                    destination.Add(new JObject { ["error"] = ex.Message });
                }
            }
        }

        private static JArray BuildCapabilityRows(JObject baseline, JObject memory,
            JObject reopened, bool importInvoked, int insertedCount,
            bool saveSucceeded, string failure, string memoryPath,
            string reopenPath, JObject nativeProbes)
        {
            var baselineMap = RecordMap(baseline);
            var memoryMap = RecordMap(memory);
            var reopenMap = RecordMap(reopened);
            var added = memoryMap.Keys.Where(key => !baselineMap.ContainsKey(key))
                .ToList();
            bool iteration = SnapshotBool(memory, "iteration_complete") &&
                SnapshotBool(reopened, "iteration_complete");
            bool stableAll = saveSucceeded && memoryMap.Count == reopenMap.Count &&
                memoryMap.All(pair => reopenMap.ContainsKey(pair.Key) &&
                    JToken.DeepEquals(pair.Value["identity_contract"],
                        reopenMap[pair.Key]["identity_contract"]));

            var rows = new JArray();
            foreach (string capability in DimensionApiProbeContract.CapabilityIds)
            {
                IList<JObject> focus = FocusRecords(capability, added,
                    memoryMap);
                bool global = capability == "display_dimension_iteration" ||
                    capability == "annotation_position" ||
                    capability == "annotation_text_bounds" ||
                    capability == "save_reopen_stable_identity";
                if (global) focus = memoryMap.Values.ToList();
                bool present = focus.Count > 0;
                bool stable = present && focus.All(record =>
                    reopenMap.ContainsKey(record.Value<string>("key")) &&
                    JToken.DeepEquals(record["identity_contract"],
                        reopenMap[record.Value<string>("key")]["identity_contract"]));
                bool attachment = present && focus.All(record =>
                {
                    JObject current;
                    return AttachmentReady(record) &&
                        reopenMap.TryGetValue(record.Value<string>("key"), out current) &&
                        OriginalReferencesResolved(current);
                });
                bool position = present && focus.All(record =>
                    ((JArray)record["position_sheet_m"]).Count >= 2);
                // F0 remains fail-closed here. IDisplayData exposes text anchors,
                // height and primitives, but the 2025 API does not expose a general
                // exact glyph width for display dimensions.
                bool exactTextBounds = present && focus.All(record =>
                    record["display_data"].Value<bool>("text_bounds_exact"));
                bool textDisplayApiObserved = present && focus.All(record =>
                    record["display_data"].Value<bool>("available"));
                bool nativeInvoked = NativeApiInvoked(capability, importInvoked,
                    memoryMap.Count > 0, nativeProbes);
                bool memoryReadback = capability == "model_dimension_import"
                    ? insertedCount > 0 && added.Count > 0
                    : present;
                bool persistence = stable && saveSucceeded;
                JObject checks = new JObject
                {
                    ["native_api_invoked"] = nativeInvoked,
                    ["in_memory_readback"] = memoryReadback,
                    ["save_close_readonly_reopen"] = persistence,
                    ["stable_identity"] = stable,
                    ["attachment_readback"] = attachment,
                    ["position_readback"] = position,
                    ["text_bounds_readback"] = exactTextBounds
                };
                bool supported = iteration && failure == null &&
                    checks.Properties().All(property => property.Value.Value<bool>());
                bool stableUnsupported = capability == "annotation_text_bounds" &&
                    iteration && failure == null && nativeInvoked &&
                    textDisplayApiObserved && !exactTextBounds;

                var evidence = new JArray();
                evidence.Add(new JObject
                {
                    ["memory_readback"] = memoryPath,
                    ["readonly_reopen_readback"] = reopenPath,
                    ["focus_count"] = focus.Count,
                    ["added_dimension_count"] = added.Count,
                    ["inserted_annotation_count"] = insertedCount,
                    ["iteration_complete"] = iteration,
                    ["stable_all"] = stableAll
                }.ToString(Formatting.None));
                if (nativeProbes != null && nativeProbes[capability] != null)
                    evidence.Add(nativeProbes[capability].ToString(Formatting.None));
                JObject stableFailures = nativeProbes != null
                    ? nativeProbes["stable_failures"] as JObject : null;
                if (stableFailures != null && stableFailures[capability] != null)
                    evidence.Add(new JObject
                    {
                        ["stable_failure"] = stableFailures[capability].DeepClone()
                    }.ToString(Formatting.None));
                var limitations = new JArray();
                if (!present) limitations.Add("no qualifying native dimension was observed");
                if (!stable) limitations.Add("save/reopen identity was not proven for this capability");
                if (!attachment) limitations.Add("non-empty persistent attachment readback was not proven");
                if (!position) limitations.Add("drawing-sheet position readback was not proven");
                if (!exactTextBounds) limitations.Add(
                    "exact glyph text bounds are unavailable; anchors and display primitives were captured only");
                if (failure != null) limitations.Add(failure);

                rows.Add(new JObject
                {
                    ["id"] = capability,
                    ["status"] = supported ? "supported" :
                        stableUnsupported ? "unsupported" : "planned",
                    ["checks"] = checks,
                    ["evidence"] = evidence,
                    ["limitations"] = limitations
                });
            }
            return rows;
        }

        private static bool NativeApiInvoked(string capability,
            bool importInvoked, bool hasDimensions, JObject nativeProbes)
        {
            switch (capability)
            {
                case "model_dimension_import":
                    return importInvoked;
                case "display_dimension_iteration":
                case "attachment_persistent_reference":
                case "annotation_position":
                case "annotation_text_bounds":
                case "save_reopen_stable_identity":
                    return hasDimensions;
                case "linear_dimension":
                case "angular_dimension":
                    return importInvoked;
                default:
                    JObject row = nativeProbes != null
                        ? nativeProbes[capability] as JObject : null;
                    return row != null && row.Value<int>("attempt_count") > 0;
            }
        }

        private static IList<JObject> FocusRecords(string capability,
            IList<string> addedKeys, IDictionary<string, JObject> records)
        {
            IEnumerable<JObject> added = addedKeys.Where(records.ContainsKey)
                .Select(key => records[key]);
            switch (capability)
            {
                case "model_dimension_import": return added.ToList();
                case "attachment_persistent_reference": return records.Values
                    .Where(record => record["attachments"].Value<int>("count") > 0)
                    .ToList();
                case "linear_dimension": return added.Where(record =>
                    new[] { 2, 11, 12 }.Contains(record.Value<int>("type"))).ToList();
                case "angular_dimension": return added.Where(record =>
                    record.Value<int>("type") == 3).ToList();
                case "radius_dimension": return added.Where(record =>
                    new[] { 5, 14 }.Contains(record.Value<int>("type"))).ToList();
                case "diameter_dimension": return added.Where(record =>
                    new[] { 6, 15 }.Contains(record.Value<int>("type"))).ToList();
                case "chamfer_dimension": return added.Where(record =>
                    record.Value<int>("type") == 10).ToList();
                case "hole_callout": return added.Where(record =>
                    record.Value<bool>("is_hole_callout")).ToList();
                case "dimension_tolerance": return added.Where(record =>
                    record["tolerance"].Value<bool>("available") &&
                    record["tolerance"].Value<int>("type") != 0).ToList();
                case "dimension_prefix_suffix": return added.Where(record =>
                    !string.IsNullOrEmpty(record["text"].Value<string>("prefix")) ||
                    !string.IsNullOrEmpty(record["text"].Value<string>("suffix"))).ToList();
                default: return records.Values.ToList();
            }
        }

        private static bool AttachmentReady(JObject record)
        {
            JObject attachments = (JObject)record["attachments"];
            int count = attachments.Value<int>("count");
            var references = (JArray)attachments["persistent_references"];
            var resolved = (JArray)attachments["resolved_in_session"];
            return count > 0 && references.Count == count && resolved.Count == count &&
                references.All(item => item.Type == JTokenType.String &&
                    !string.IsNullOrEmpty(item.Value<string>())) &&
                resolved.All(item => item.Value<bool>());
        }

        private static bool OriginalReferencesResolved(JObject reopenedRecord)
        {
            var results = reopenedRecord[
                "original_persistent_references_after_reopen"] as JArray;
            if (results == null || results.Count == 0) return false;
            return results.OfType<JObject>().Count() == results.Count &&
                results.OfType<JObject>().All(item =>
                    item.Value<bool>("resolved") && item.Value<int>("state") == 0);
        }

        private static void VerifyOriginalPersistReferences(IModelDoc2 reopenedModel,
            JObject memory, JObject reopened)
        {
            var reopenMap = RecordMap(reopened);
            var referencedDocuments = ReferencedDocumentsByView(reopenedModel);
            foreach (JObject original in (JArray)memory["records"])
            {
                JObject current;
                if (!reopenMap.TryGetValue(original.Value<string>("key"), out current))
                    continue;
                var results = new JArray();
                JArray drawingReferences = (JArray)original["attachments"]
                    ["persistent_references"];
                JArray modelReferences = original["attachments"]
                    ["referenced_model_persistent_references"] as JArray ??
                    new JArray();
                for (int index = 0; index < drawingReferences.Count; index++)
                {
                    JToken token = drawingReferences[index];
                    int state;
                    object resolved = ResolveReference(reopenedModel.Extension,
                        token.Type == JTokenType.String ? token.Value<string>() : null,
                        out state);
                    string domain = "drawing";
                    string source = "drawing";
                    IModelDoc2 referenced;
                    if (resolved == null || state != 0)
                    {
                        if (referencedDocuments.TryGetValue(
                            original.Value<string>("view") ?? "", out referenced) &&
                            referenced != null)
                        {
                            int referencedState;
                            object referencedResolved = ResolveReference(
                                referenced.Extension,
                                token.Type == JTokenType.String
                                    ? token.Value<string>() : null,
                                out referencedState);
                            if (referencedResolved != null && referencedState == 0)
                            {
                                resolved = referencedResolved;
                                state = referencedState;
                                domain = "referenced_model";
                            }
                        }
                    }
                    if ((resolved == null || state != 0) &&
                        index < modelReferences.Count &&
                        modelReferences[index].Type == JTokenType.String &&
                        referencedDocuments.TryGetValue(
                            original.Value<string>("view") ?? "", out referenced) &&
                        referenced != null)
                    {
                        int modelState;
                        object modelResolved = ResolveReference(
                            referenced.Extension,
                            modelReferences[index].Value<string>(), out modelState);
                        if (modelResolved != null && modelState == 0)
                        {
                            resolved = modelResolved;
                            state = modelState;
                            domain = "referenced_model";
                            source = "referenced_model";
                        }
                    }
                    results.Add(new JObject
                    {
                        ["resolved"] = resolved != null && state == 0,
                        ["state"] = state,
                        ["reference_source"] = source,
                        ["resolution_domain"] = domain
                    });
                }
                current["original_persistent_references_after_reopen"] = results;
            }
        }

        private static Dictionary<string, IModelDoc2> ReferencedDocumentsByView(
            IModelDoc2 drawingModel)
        {
            var result = new Dictionary<string, IModelDoc2>(
                StringComparer.OrdinalIgnoreCase);
            var drawing = drawingModel as IDrawingDoc;
            IView view = drawing != null ? drawing.GetFirstView() as IView : null;
            int guard = 0;
            while (view != null && guard++ < 2000)
            {
                try
                {
                    string name = view.Name ?? "";
                    IModelDoc2 referenced = view.ReferencedDocument as IModelDoc2;
                    if (!string.IsNullOrEmpty(name) && referenced != null &&
                        !result.ContainsKey(name))
                        result.Add(name, referenced);
                }
                catch { }
                view = view.GetNextView() as IView;
            }
            return result;
        }

        private static Dictionary<string, JObject> RecordMap(JObject snapshot)
        {
            var result = new Dictionary<string, JObject>(
                StringComparer.OrdinalIgnoreCase);
            JArray records = snapshot != null ? snapshot["records"] as JArray : null;
            if (records == null) return result;
            foreach (JObject record in records.OfType<JObject>())
            {
                string key = record.Value<string>("key");
                if (!string.IsNullOrEmpty(key) && !result.ContainsKey(key))
                    result.Add(key, record);
            }
            return result;
        }

        private static JArray BuildUpstreamRows(DimensionApiProbeRequest request)
        {
            var result = new JArray();
            if (request.SourceKind == "research_model_drawing_pair")
            {
                result.Add(Upstream("source_model", request.SourceModel));
                result.Add(Upstream("source_drawing", request.SourceDrawing));
                result.Add(Upstream("drawing_template", request.DrawingTemplate));
            }
            else
            {
                result.Add(Upstream("view_plan", request.ViewPlan));
                result.Add(Upstream("verified_drawing", request.VerifiedDrawing));
                result.Add(Upstream("verification_sidecar",
                    request.VerificationSidecar));
            }
            return result;
        }

        private static JObject Upstream(string role,
            DimensionApiProbeArtifact artifact)
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
            {
                string path = row.Value<string>("path");
                row["sha256_after"] = File.Exists(path) ? FileSha256(path) :
                    new string('0', 64);
            }
        }

        private static JObject SolidWorksIdentity(string revision)
        {
            string[] parts = (revision ?? "").Split('.');
            int revisionMajor;
            int servicePack;
            int.TryParse(parts.Length > 0 ? parts[0] : "0", out revisionMajor);
            int.TryParse(parts.Length > 1 ? parts[1] : "0", out servicePack);
            return new JObject
            {
                ["major_version"] = revisionMajor > 0 ? revisionMajor + 1992 : 2025,
                ["service_pack"] = "SP" + servicePack,
                ["revision"] = revision ?? "unknown"
            };
        }

        private string SafeRevision()
        {
            try { return _solidWorks.RevisionNumber(); }
            catch { return "unknown"; }
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

        private void RestoreActiveDocument(string title)
        {
            if (string.IsNullOrEmpty(title)) return;
            try
            {
                int errors = 0;
                _solidWorks.ActivateDoc3(title, false, 1, ref errors);
            }
            catch { }
        }

        private void CloseProbeDocument(IModelDoc2 document)
        {
            if (document == null) return;
            try { _solidWorks.CloseDoc(document.GetTitle()); } catch { }
        }

        private void CloseExactPathIfOpen(string path)
        {
            try
            {
                var document = _solidWorks.GetOpenDocumentByName(path) as IModelDoc2;
                if (document != null && string.Equals(document.GetPathName(), path,
                        StringComparison.OrdinalIgnoreCase))
                    _solidWorks.CloseDoc(document.GetTitle());
            }
            catch { }
        }

        private static void RebuildForReadback(IModelDoc2 model)
        {
            try { model.ForceRebuild3(false); } catch { }
            try { model.EditRebuild3(); } catch { }
            try { model.GraphicsRedraw2(); } catch { }
        }

        private static JObject EmptySnapshot(string phase)
        {
            return new JObject
            {
                ["phase"] = phase,
                ["view_count"] = 0,
                ["expected_dimension_count"] = 0,
                ["traversed_dimension_count"] = 0,
                ["iteration_complete"] = false,
                ["records"] = new JArray()
            };
        }

        private static bool SnapshotBool(JObject snapshot, string name)
        {
            return snapshot != null && snapshot.Value<bool?>(name) == true;
        }

        private static int CountArray(object value)
        {
            var array = value as Array;
            return array != null ? array.Length : value == null ? 0 : 1;
        }

        private static JArray ToJsonArray(object value)
        {
            var result = new JArray();
            var array = value as Array;
            if (array == null)
            {
                if (value != null) result.Add(Convert.ToString(value,
                    CultureInfo.InvariantCulture));
                return result;
            }
            foreach (object item in array)
            {
                if (item == null) result.Add(JValue.CreateNull());
                else if (item is bool) result.Add((bool)item);
                else if (item is string) result.Add((string)item);
                else if (item is IConvertible)
                    result.Add(Convert.ToString(item, CultureInfo.InvariantCulture));
                else result.Add(item.GetType().FullName);
            }
            return result;
        }

        private static string[] ToStringArray(object value)
        {
            if (value is string[]) return (string[])value;
            var array = value as Array;
            if (array == null) return new string[0];
            var result = new string[array.Length];
            for (int index = 0; index < array.Length; index++)
                result[index] = Convert.ToString(array.GetValue(index),
                    CultureInfo.InvariantCulture) ?? "";
            return result;
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

        private static JArray ToRoundedArray(object value)
        {
            return new JArray(ToDoubleArray(value).Select(item => Round(item)));
        }

        private static JToken JNumber(double value)
        {
            return double.IsNaN(value) || double.IsInfinity(value)
                ? JValue.CreateNull() : (JToken)Round(value);
        }

        private static double Round(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value)) return value;
            return Math.Round(value, 9, MidpointRounding.AwayFromZero);
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

        private static object ResolveReference(IModelDocExtension extension,
            string persistReference, out int state)
        {
            state = 1;
            if (extension == null || string.IsNullOrEmpty(persistReference))
                return null;
            try
            {
                return extension.GetObjectByPersistReference3(
                    Convert.FromBase64String(persistReference), out state);
            }
            catch { return null; }
        }

        private static void AtomicWriteJson(string path, JToken value)
        {
            string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
            File.WriteAllText(temporary,
                value.ToString(Formatting.Indented) + System.Environment.NewLine,
                new UTF8Encoding(false));
            if (File.Exists(path))
                throw new IOException("refusing to overwrite F0 artifact: " + path);
            File.Move(temporary, path);
        }

        private static string FileSha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(stream)
                    .Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private static string Sha256Text(string value)
        {
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value))
                    .Select(item => item.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private static string CanonicalSha256(JToken value)
        {
            return Sha256Text(Canonicalize(value).ToString(Formatting.None));
        }

        private static JToken Canonicalize(JToken value)
        {
            var obj = value as JObject;
            if (obj != null)
            {
                var result = new JObject();
                foreach (JProperty property in obj.Properties()
                    .OrderBy(item => item.Name, StringComparer.Ordinal))
                    result[property.Name] = Canonicalize(property.Value);
                return result;
            }
            var array = value as JArray;
            if (array != null)
                return new JArray(array.Select(Canonicalize));
            return value.DeepClone();
        }
    }
}
