using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Infrastructure;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Transactional, engineering-semantic drawing operations.
    ///
    /// The public operation is one frozen drawing plan. COM-level primitives (document activation,
    /// selection, view creation, style application, rebuild, save, reopen, and read-back) remain
    /// private to this executor. A plan either commits one verified SLDdrw or leaves the requested
    /// output untouched.
    /// </summary>
    public partial class SolidWorksService
    {
        private const string DrawingPlanSchemaVersion = "1.0";
        private const string DrawingViewNamePrefix = "Q3DS_";
        private static readonly Regex DrawingViewIdPattern =
            new Regex("^[A-Za-z][A-Za-z0-9_-]{0,63}$", RegexOptions.CultureInvariant);

        private sealed class DrawingPlanSpec
        {
            public string ModelPath;
            public string Configuration;
            public string DisplayState;
            public string TemplatePath;
            public string OutputPath;
            public bool Overwrite;
            public string Projection;
            public double SheetScaleNumerator;
            public double SheetScaleDenominator;
            public double SheetMargin;
            public double ViewClearance;
            public double PositionTolerance;
            public double ScaleTolerance;
            public bool RequireNoOverlap;
            public List<DrawingViewPlanSpec> Views;
            public JObject CanonicalPlan;
            public string PlanSha256;
        }

        private sealed class DrawingViewPlanSpec
        {
            public string Id;
            public string Kind;
            public string Orientation;
            public string ParentId;
            public double X;
            public double Y;
            public string ScaleMode;
            public double? Scale;
            public string DisplayMode;
            public string TangentEdges;
            public string Configuration;
            public string DisplayState;
            public bool LockPosition;
        }

        private sealed class OpenModelScope
        {
            public IModelDoc2 Document;
            public bool OpenedByExecutor;
            public string Configuration;
            public Dictionary<string, string> StandardViewNames;
        }

        public ExecutionResponse InspectPartForDrawing(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");

            var p = request.Params as JObject;
            string rawPath = p != null ? p.Value<string>("model_path") : null;
            string modelPath;
            string pathError;
            if (!TryValidateExistingAbsolutePath(rawPath, new[] { ".SLDPRT" }, out modelPath, out pathError))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_MODEL_PATH", pathError);

            string previousTitle = GetActiveDocumentTitle();
            OpenModelScope scope = null;
            try
            {
                string openError;
                scope = OpenModelForDrawing(modelPath, "", out openError);
                if (scope == null)
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "MODEL_OPEN_FAILED", openError);

                var part = scope.Document as IPartDoc;
                if (part == null)
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "NOT_A_PART", "The source document is not a SolidWorks part.");

                var configurations = new JArray();
                var rawConfigurations = scope.Document.GetConfigurationNames() as Array;
                if (rawConfigurations != null)
                {
                    foreach (object item in rawConfigurations)
                        configurations.Add(Convert.ToString(item, CultureInfo.InvariantCulture));
                }

                var views = new JObject();
                foreach (var pair in scope.StandardViewNames)
                    views[pair.Key] = pair.Value;

                var result = new JObject
                {
                    ["schema_version"] = DrawingPlanSchemaVersion,
                    ["model_path"] = modelPath,
                    ["configuration"] = scope.Configuration,
                    ["configurations"] = configurations,
                    ["standard_view_names"] = views,
                    ["has_unsaved_changes"] = scope.Document.GetSaveFlag()
                };

                try
                {
                    var box = part.GetPartBox(true) as double[];
                    if (box != null && box.Length >= 6)
                    {
                        result["bounding_box_m"] = new JArray(box.Select(R6));
                        result["size_m"] = new JArray
                        {
                            R6(Math.Abs(box[3] - box[0])),
                            R6(Math.Abs(box[4] - box[1])),
                            R6(Math.Abs(box[5] - box[2]))
                        };
                    }
                }
                catch { }

                return new ExecutionResponse
                {
                    OperationId = request.OperationId,
                    Status = "COMPLETED",
                    Verified = true,
                    StateVersion = _guard.GetCurrentStateVersion(),
                    CadState = BuildCurrentCadState(_guard.GetCurrentStateVersion()),
                    ResultGeometry = result
                };
            }
            catch (Exception ex)
            {
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INSPECTION_FAILED", ex.Message);
            }
            finally
            {
                CloseModelScope(scope);
                RestoreActiveDocument(previousTitle);
            }
        }

        public ExecutionResponse ExecuteDrawingPlan(ToolRequest request)
        {
            if (_guard.IsDuplicate(request.OperationId))
                return _guard.GetDuplicate(request.OperationId);
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");

            DrawingPlanSpec plan;
            string validationCode;
            string validationMessage;
            if (!TryParseDrawingPlan(request.Params as JObject, out plan,
                out validationCode, out validationMessage))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    validationCode, validationMessage);

            JObject transactionResult;
            string errorCode;
            string errorMessage;
            if (!TryExecuteDrawingTransaction(plan, request.OperationId,
                out transactionResult, out errorCode, out errorMessage))
                return BuildDrawingFailure(request.OperationId, errorCode, errorMessage, transactionResult);

            int nextState = _guard.GetCurrentStateVersion() + 1;
            var response = new ExecutionResponse
            {
                OperationId = request.OperationId,
                Status = "COMPLETED",
                Verified = true,
                StateVersion = nextState,
                CadState = BuildCurrentCadState(nextState),
                ResultGeometry = transactionResult
            };
            if (response.CadState != null)
                response.CadState.Features = new List<string> { plan.OutputPath };
            _guard.RegisterCompleted(request.OperationId, response);
            return response;
        }

        public ExecutionResponse VerifyDrawingPlan(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");

            DrawingPlanSpec plan;
            string validationCode;
            string validationMessage;
            if (!TryParseDrawingPlan(request.Params as JObject, out plan,
                out validationCode, out validationMessage))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    validationCode, validationMessage);
            if (!File.Exists(plan.OutputPath))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "DRAWING_NOT_FOUND", "The drawing output does not exist: " + plan.OutputPath);

            string previousTitle = GetActiveDocumentTitle();
            OpenModelScope modelScope = null;
            try
            {
                string modelError;
                modelScope = OpenModelForDrawing(plan.ModelPath, plan.Configuration, out modelError);
                if (modelScope == null)
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "MODEL_PREFLIGHT_FAILED", modelError);
                plan.Configuration = modelScope.Configuration;
                foreach (var viewSpec in plan.Views)
                {
                    if (string.IsNullOrEmpty(viewSpec.Configuration))
                        viewSpec.Configuration = plan.Configuration;
                    if (modelScope.Document.GetConfigurationByName(viewSpec.Configuration) == null)
                        return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                            "VIEW_CONFIGURATION_NOT_FOUND", "View '" + viewSpec.Id +
                            "' references missing configuration '" + viewSpec.Configuration + "'.");
                    if (string.IsNullOrEmpty(viewSpec.DisplayState))
                        viewSpec.DisplayState = plan.DisplayState;
                }

                Dictionary<string, string> expectedHandles;
                string expectedArtifactHash;
                string reportError;
                if (!TryLoadVerificationReport(plan, out expectedHandles,
                    out expectedArtifactHash, out reportError))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "VERIFICATION_REPORT_INVALID", reportError);
                string actualArtifactHash = ComputeFileSha256(plan.OutputPath);
                if (!string.Equals(actualArtifactHash, expectedArtifactHash,
                    StringComparison.OrdinalIgnoreCase))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "ARTIFACT_HASH_MISMATCH", "The drawing SHA-256 no longer matches its verification report.");

                JObject snapshot;
                string errorCode;
                string errorMessage;
                if (!TryVerifyDrawingFile(plan.OutputPath, plan, expectedHandles,
                    out snapshot, out errorCode, out errorMessage))
                    return BuildDrawingFailure(request.OperationId, errorCode, errorMessage, snapshot);

                snapshot["artifact_sha256"] = actualArtifactHash;
                snapshot["plan_sha256"] = plan.PlanSha256;
                return new ExecutionResponse
                {
                    OperationId = request.OperationId,
                    Status = "COMPLETED",
                    Verified = true,
                    StateVersion = _guard.GetCurrentStateVersion(),
                    CadState = BuildCurrentCadState(_guard.GetCurrentStateVersion()),
                    ResultGeometry = snapshot
                };
            }
            finally
            {
                CloseModelScope(modelScope);
                RestoreActiveDocument(previousTitle);
            }
        }

        private bool TryExecuteDrawingTransaction(DrawingPlanSpec plan, string operationId,
            out JObject result, out string errorCode, out string errorMessage)
        {
            result = new JObject
            {
                ["plan_sha256"] = plan.PlanSha256,
                ["output_path"] = plan.OutputPath,
                ["committed"] = false
            };
            errorCode = null;
            errorMessage = null;

            if (File.Exists(plan.OutputPath) && !plan.Overwrite)
            {
                errorCode = "OUTPUT_EXISTS";
                errorMessage = "The requested output already exists and drawing.overwrite is false: " +
                    plan.OutputPath;
                return false;
            }

            if (_solidWorks.GetOpenDocumentByName(plan.OutputPath) != null)
            {
                errorCode = "OUTPUT_DOCUMENT_OPEN";
                errorMessage = "The requested output drawing is already open in SolidWorks.";
                return false;
            }

            string previousTitle = GetActiveDocumentTitle();
            string outputDirectory = Path.GetDirectoryName(plan.OutputPath);
            string outputStem = Path.GetFileNameWithoutExtension(plan.OutputPath);
            string safeOperation = Regex.Replace(operationId ?? "operation", "[^A-Za-z0-9_-]", "_");
            if (safeOperation.Length > 48) safeOperation = safeOperation.Substring(0, 48);
            string tempPath = Path.Combine(outputDirectory,
                "." + outputStem + "." + safeOperation + ".tmp.SLDDRW");
            string reportPath = plan.OutputPath + ".verification.json";
            string reportTempPath = reportPath + "." + safeOperation + ".tmp";

            IModelDoc2 drawingModel = null;
            OpenModelScope modelScope = null;
            bool committed = false;
            try
            {
                DeleteGeneratedFileIfPresent(tempPath);
                DeleteGeneratedFileIfPresent(reportTempPath);

                string modelError;
                modelScope = OpenModelForDrawing(plan.ModelPath, plan.Configuration, out modelError);
                if (modelScope == null)
                {
                    errorCode = "MODEL_PREFLIGHT_FAILED";
                    errorMessage = modelError;
                    return false;
                }
                if (modelScope.Document.GetSaveFlag())
                {
                    errorCode = "MODEL_HAS_UNSAVED_CHANGES";
                    errorMessage = "The source model has unsaved changes. Save it before executing a frozen drawing plan.";
                    return false;
                }
                plan.Configuration = modelScope.Configuration;
                foreach (var viewSpec in plan.Views)
                {
                    if (string.IsNullOrEmpty(viewSpec.Configuration))
                        viewSpec.Configuration = plan.Configuration;
                    if (modelScope.Document.GetConfigurationByName(viewSpec.Configuration) == null)
                    {
                        errorCode = "VIEW_CONFIGURATION_NOT_FOUND";
                        errorMessage = "View '" + viewSpec.Id + "' references missing configuration '" +
                            viewSpec.Configuration + "'.";
                        return false;
                    }
                    if (string.IsNullOrEmpty(viewSpec.DisplayState))
                        viewSpec.DisplayState = plan.DisplayState;
                }

                if (File.Exists(reportPath) && !plan.Overwrite)
                {
                    errorCode = "OUTPUT_REPORT_EXISTS";
                    errorMessage = "The verification sidecar already exists and drawing.overwrite is false: " +
                        reportPath;
                    return false;
                }

                drawingModel = _solidWorks.NewDocument(plan.TemplatePath, 0, 0.0, 0.0) as IModelDoc2;
                var drawing = drawingModel as IDrawingDoc;
                if (drawing == null)
                {
                    errorCode = "DRAWING_CREATION_FAILED";
                    errorMessage = "SolidWorks did not create a drawing from the supplied template.";
                    return false;
                }

                var sheet = drawing.GetCurrentSheet() as ISheet;
                if (sheet == null)
                {
                    errorCode = "SHEET_NOT_FOUND";
                    errorMessage = "The new drawing has no active sheet.";
                    return false;
                }

                double sheetWidth;
                double sheetHeight;
                string sheetError;
                if (!ConfigureSheet(sheet, plan, out sheetWidth, out sheetHeight, out sheetError))
                {
                    errorCode = "SHEET_CONFIGURATION_FAILED";
                    errorMessage = sheetError;
                    return false;
                }

                var createdViews = new Dictionary<string, IView>(StringComparer.Ordinal);
                var expectedHandles = new Dictionary<string, string>(StringComparer.Ordinal);
                foreach (var viewSpec in plan.Views)
                {
                    IView view;
                    string createError;
                    if (!TryCreatePlannedView(drawingModel, drawing, modelScope, viewSpec,
                        createdViews, out view, out createError))
                    {
                        errorCode = "VIEW_CREATION_FAILED";
                        errorMessage = "View '" + viewSpec.Id + "': " + createError;
                        return false;
                    }

                    string styleError;
                    if (!TryApplyViewContract(drawingModel, view, viewSpec, modelScope.Configuration,
                        out styleError))
                    {
                        errorCode = "VIEW_CONFIGURATION_FAILED";
                        errorMessage = "View '" + viewSpec.Id + "': " + styleError;
                        return false;
                    }

                    createdViews.Add(viewSpec.Id, view);
                    string unique = SafeViewUniqueName(view);
                    if (string.IsNullOrEmpty(unique))
                    {
                        errorCode = "VIEW_HANDLE_UNAVAILABLE";
                        errorMessage = "SolidWorks did not provide a persistent unique handle for view '" +
                            viewSpec.Id + "'.";
                        return false;
                    }
                    expectedHandles.Add(viewSpec.Id, unique);
                }

                drawingModel.ClearSelection2(true);
                if (!drawingModel.ForceRebuild3(false))
                    ExecLog.Write("execute_drawing_plan: ForceRebuild3 returned false; continuing to read-back verification");
                drawingModel.GraphicsRedraw2();

                JObject inMemorySnapshot;
                List<string> inMemoryErrors;
                if (!TryBuildDrawingSnapshot(drawing, plan, expectedHandles, sheetWidth, sheetHeight,
                    out inMemorySnapshot, out inMemoryErrors))
                {
                    result["in_memory_verification"] = inMemorySnapshot;
                    errorCode = "IN_MEMORY_VERIFICATION_FAILED";
                    errorMessage = string.Join("; ", inMemoryErrors);
                    return false;
                }
                result["in_memory_verification"] = inMemorySnapshot;

                int saveErrors = 0;
                int saveWarnings = 0;
                bool saved = drawingModel.Extension.SaveAs3(tempPath, 0, 1,
                    null, null, ref saveErrors, ref saveWarnings);
                if (!saved || saveErrors != 0 || !File.Exists(tempPath))
                {
                    errorCode = "DRAWING_SAVE_FAILED";
                    errorMessage = "SaveAs3 failed for the transaction file (errors=" + saveErrors +
                        ", warnings=" + saveWarnings + ").";
                    return false;
                }

                string drawingTitle = drawingModel.GetTitle();
                drawingModel.ClearSelection2(true);
                _solidWorks.CloseDoc(drawingTitle);
                drawingModel = null;

                JObject reopenedSnapshot;
                string verifyCode;
                string verifyMessage;
                if (!TryVerifyDrawingFile(tempPath, plan, expectedHandles,
                    out reopenedSnapshot, out verifyCode, out verifyMessage))
                {
                    result["reopen_verification"] = reopenedSnapshot;
                    errorCode = verifyCode;
                    errorMessage = verifyMessage;
                    return false;
                }
                result["reopen_verification"] = reopenedSnapshot;

                string artifactHash = ComputeFileSha256(tempPath);
                var report = new JObject
                {
                    ["schema_version"] = "1.0",
                    ["operation_id"] = operationId,
                    ["generated_at_utc"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                    ["plan_sha256"] = plan.PlanSha256,
                    ["artifact_sha256"] = artifactHash,
                    ["output_path"] = plan.OutputPath,
                    ["verified"] = true,
                    ["view_handles"] = JObject.FromObject(expectedHandles),
                    ["verification"] = reopenedSnapshot
                };
                File.WriteAllText(reportTempPath, report.ToString(Formatting.Indented),
                    new UTF8Encoding(false));

                CommitGeneratedFile(tempPath, plan.OutputPath, plan.Overwrite);
                CommitGeneratedFile(reportTempPath, reportPath, true);
                committed = true;
                result["committed"] = true;
                result["artifact_sha256"] = artifactHash;
                result["verification_report"] = reportPath;
                result["view_handles"] = JObject.FromObject(expectedHandles);
                return true;
            }
            catch (Exception ex)
            {
                errorCode = "DRAWING_TRANSACTION_FAILED";
                errorMessage = ex.Message;
                ExecLog.Write("execute_drawing_plan transaction exception: " + ex);
                return false;
            }
            finally
            {
                try
                {
                    if (drawingModel != null)
                    {
                        drawingModel.ClearSelection2(true);
                        _solidWorks.CloseDoc(drawingModel.GetTitle());
                    }
                }
                catch { }
                CloseModelScope(modelScope);
                RestoreActiveDocument(previousTitle);
                if (!committed)
                {
                    DeleteGeneratedFileIfPresent(tempPath);
                    DeleteGeneratedFileIfPresent(reportTempPath);
                }
            }
        }

        private bool TryCreatePlannedView(IModelDoc2 drawingModel, IDrawingDoc drawing,
            OpenModelScope modelScope, DrawingViewPlanSpec spec,
            Dictionary<string, IView> createdViews, out IView view, out string error)
        {
            view = null;
            error = null;
            drawingModel.ClearSelection2(true);
            if (spec.Kind == "base")
            {
                string actualViewName;
                if (!modelScope.StandardViewNames.TryGetValue(spec.Orientation, out actualViewName))
                {
                    error = "The source model does not expose the requested standard orientation '" +
                        spec.Orientation + "'.";
                    return false;
                }
                view = drawing.CreateDrawViewFromModelView3(modelScope.Document.GetPathName(),
                    actualViewName, spec.X, spec.Y, 0.0) as IView;
            }
            else
            {
                IView parent;
                if (!createdViews.TryGetValue(spec.ParentId, out parent))
                {
                    error = "Parent view '" + spec.ParentId + "' has not been created.";
                    return false;
                }
                drawing.ActivateView(parent.Name);
                var parentPosition = parent.Position as double[];
                double selectX = parentPosition != null && parentPosition.Length >= 2 ? parentPosition[0] : 0.0;
                double selectY = parentPosition != null && parentPosition.Length >= 2 ? parentPosition[1] : 0.0;
                bool selected = drawingModel.Extension.SelectByID2(parent.Name, "DRAWINGVIEW",
                    selectX, selectY, 0.0, false, 0, null, 0);
                if (!selected)
                {
                    error = "The parent drawing view could not be selected inside the isolated selection scope.";
                    return false;
                }
                view = drawing.CreateUnfoldedViewAt3(spec.X, spec.Y, 0.0, false) as IView;
            }
            drawingModel.ClearSelection2(true);
            if (view == null)
            {
                error = "The SolidWorks view-creation API returned null.";
                return false;
            }
            if (!view.SetName2(DrawingViewNamePrefix + spec.Id))
            {
                error = "The created view could not be assigned its deterministic display name.";
                return false;
            }
            return true;
        }

        private bool TryApplyViewContract(IModelDoc2 drawingModel, IView view,
            DrawingViewPlanSpec spec, string defaultConfiguration, out string error)
        {
            error = null;
            try
            {
                view.PositionLocked = false;
                view.Position = new[] { spec.X, spec.Y };

                string configuration = string.IsNullOrEmpty(spec.Configuration)
                    ? defaultConfiguration : spec.Configuration;
                if (spec.Kind == "projected")
                {
                    view.LinkParentConfiguration = true;
                }
                else
                {
                    view.LinkParentConfiguration = false;
                    view.ReferencedConfiguration = configuration;
                }
                if (!string.IsNullOrEmpty(spec.DisplayState))
                    view.DisplayState = spec.DisplayState;

                if (spec.ScaleMode == "parent")
                {
                    view.UseParentScale = true;
                }
                else if (spec.ScaleMode == "sheet")
                {
                    view.UseParentScale = false;
                    view.UseSheetScale = 1;
                }
                else
                {
                    view.UseParentScale = false;
                    view.UseSheetScale = 0;
                    view.ScaleDecimal = spec.Scale.Value;
                }

                int displayMode = DisplayModeValue(spec.DisplayMode);
                // The 2025 API represents shaded-with-edges as swSHADED + Edges=true on write,
                // while GetDisplayMode2 reads it back as swSHADED_EDGES.
                int writeDisplayMode = spec.DisplayMode == "shaded_with_edges" ? 3 : displayMode;
                bool displayApplied = view.SetDisplayMode3(false, writeDisplayMode, false,
                    spec.DisplayMode == "shaded_with_edges");
                if (!displayApplied)
                {
                    error = "SetDisplayMode3 rejected display_mode='" + spec.DisplayMode + "'.";
                    return false;
                }
                view.SetDisplayTangentEdges2(TangentEdgeValue(spec.TangentEdges));
                view.PositionLocked = spec.LockPosition;
                drawingModel.ForceRebuild3(false);
                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
        }

        private bool TryVerifyDrawingFile(string path, DrawingPlanSpec plan,
            Dictionary<string, string> expectedHandles, out JObject snapshot,
            out string errorCode, out string errorMessage)
        {
            snapshot = new JObject { ["path"] = path, ["verified"] = false };
            errorCode = null;
            errorMessage = null;
            string previousTitle = GetActiveDocumentTitle();
            IModelDoc2 document = null;
            try
            {
                if (_solidWorks.GetOpenDocumentByName(path) != null)
                {
                    errorCode = "VERIFICATION_DOCUMENT_ALREADY_OPEN";
                    errorMessage = "A disk-level verification requires the drawing to be closed first.";
                    return false;
                }
                int errors = 0;
                int warnings = 0;
                document = _solidWorks.OpenDoc6(path, 3, 3, "", ref errors, ref warnings) as IModelDoc2;
                var drawing = document as IDrawingDoc;
                if (drawing == null)
                {
                    errorCode = "DRAWING_REOPEN_FAILED";
                    errorMessage = "OpenDoc6 did not return a drawing (errors=" + errors +
                        ", warnings=" + warnings + ").";
                    return false;
                }
                document.ForceRebuild3(false);
                document.GraphicsRedraw2();
                var sheet = drawing.GetCurrentSheet() as ISheet;
                double width;
                double height;
                string dimensionError;
                if (!TryReadSheetSize(sheet, out width, out height, out dimensionError))
                {
                    errorCode = "SHEET_READBACK_FAILED";
                    errorMessage = dimensionError;
                    return false;
                }
                List<string> verificationErrors;
                if (!TryBuildDrawingSnapshot(drawing, plan, expectedHandles, width, height,
                    out snapshot, out verificationErrors))
                {
                    errorCode = "DRAWING_VERIFICATION_FAILED";
                    errorMessage = string.Join("; ", verificationErrors);
                    return false;
                }
                snapshot["verified"] = true;
                snapshot["open_errors"] = errors;
                snapshot["open_warnings"] = warnings;
                return true;
            }
            catch (Exception ex)
            {
                errorCode = "DRAWING_VERIFICATION_FAILED";
                errorMessage = ex.Message;
                return false;
            }
            finally
            {
                try { if (document != null) _solidWorks.CloseDoc(document.GetTitle()); } catch { }
                RestoreActiveDocument(previousTitle);
            }
        }

        private bool TryBuildDrawingSnapshot(IDrawingDoc drawing, DrawingPlanSpec plan,
            Dictionary<string, string> expectedHandles, double sheetWidth, double sheetHeight,
            out JObject snapshot, out List<string> errors)
        {
            errors = new List<string>();
            snapshot = new JObject
            {
                ["sheet_width_m"] = R6(sheetWidth),
                ["sheet_height_m"] = R6(sheetHeight),
                ["views"] = new JArray()
            };

            var liveViews = new List<IView>();
            object viewObject = drawing.GetFirstView();
            int viewGuard = 0;
            while (viewObject != null && viewGuard++ < 256)
            {
                var live = viewObject as IView;
                object next = live != null ? live.GetNextView() : null;
                if (live != null && live.Type != 1)
                    liveViews.Add(live);
                viewObject = next;
            }
            if (liveViews.Count != plan.Views.Count)
                errors.Add("Expected " + plan.Views.Count + " model views but found " + liveViews.Count + ".");

            var matched = new Dictionary<string, IView>(StringComparer.Ordinal);
            var outlines = new Dictionary<string, double[]>(StringComparer.Ordinal);
            var viewsArray = (JArray)snapshot["views"];
            foreach (var spec in plan.Views)
            {
                IView live = null;
                string expectedHandle = null;
                if (expectedHandles != null && expectedHandles.TryGetValue(spec.Id, out expectedHandle))
                    live = liveViews.FirstOrDefault(v =>
                        string.Equals(SafeViewUniqueName(v), expectedHandle, StringComparison.Ordinal));
                if (live == null)
                    live = liveViews.FirstOrDefault(v => string.Equals(v.Name,
                        DrawingViewNamePrefix + spec.Id, StringComparison.Ordinal));
                if (live == null)
                {
                    errors.Add("View '" + spec.Id + "' was not found by persistent handle or deterministic name.");
                    continue;
                }
                matched[spec.Id] = live;

                var position = live.Position as double[];
                var outline = live.GetOutline() as double[];
                var row = new JObject
                {
                    ["id"] = spec.Id,
                    ["name"] = live.Name,
                    ["unique_name"] = SafeViewUniqueName(live),
                    ["type"] = live.Type,
                    ["position_m"] = position != null ? new JArray(position.Select(R6)) : null,
                    ["outline_m"] = outline != null ? new JArray(outline.Select(R6)) : null,
                    ["scale"] = R6(live.ScaleDecimal),
                    ["configuration"] = live.ReferencedConfiguration,
                    ["display_state"] = live.DisplayState,
                    ["display_mode"] = live.GetDisplayMode2(),
                    ["edges_in_shaded_mode"] = live.GetDisplayEdgesInShadedMode(),
                    ["tangent_edges"] = live.GetDisplayTangentEdges2(),
                    ["position_locked"] = live.PositionLocked,
                    ["use_sheet_scale"] = live.UseSheetScale,
                    ["use_parent_scale"] = live.UseParentScale,
                    ["referenced_model"] = live.GetReferencedModelName()
                };
                var baseView = live.GetBaseView() as IView;
                if (baseView != null)
                {
                    row["parent_name"] = baseView.Name;
                    row["parent_unique_name"] = SafeViewUniqueName(baseView);
                }
                viewsArray.Add(row);

                if (position == null || position.Length < 2)
                    errors.Add("View '" + spec.Id + "' has no readable position.");
                else
                {
                    if (Math.Abs(position[0] - spec.X) > plan.PositionTolerance ||
                        Math.Abs(position[1] - spec.Y) > plan.PositionTolerance)
                        errors.Add("View '" + spec.Id + "' position read-back differs from the plan.");
                }

                double expectedScale = plan.SheetScaleNumerator / plan.SheetScaleDenominator;
                if (spec.ScaleMode == "custom") expectedScale = spec.Scale.Value;
                if (spec.ScaleMode == "parent")
                {
                    IView parent;
                    if (matched.TryGetValue(spec.ParentId, out parent))
                        expectedScale = parent.ScaleDecimal;
                }
                if (Math.Abs(live.ScaleDecimal - expectedScale) > plan.ScaleTolerance)
                    errors.Add("View '" + spec.Id + "' scale read-back differs from the plan.");

                string expectedConfig = string.IsNullOrEmpty(spec.Configuration)
                    ? plan.Configuration : spec.Configuration;
                if (!string.IsNullOrEmpty(expectedConfig) &&
                    !string.Equals(live.ReferencedConfiguration, expectedConfig,
                        StringComparison.OrdinalIgnoreCase))
                    errors.Add("View '" + spec.Id + "' references configuration '" +
                        live.ReferencedConfiguration + "', expected '" + expectedConfig + "'.");

                int expectedDisplayMode = spec.DisplayMode == "shaded_with_edges"
                    ? 3 : DisplayModeValue(spec.DisplayMode);
                if (live.GetDisplayMode2() != expectedDisplayMode)
                    errors.Add("View '" + spec.Id + "' display mode read-back differs from the plan.");
                if ((spec.DisplayMode == "shaded_with_edges") != live.GetDisplayEdgesInShadedMode())
                    errors.Add("View '" + spec.Id + "' shaded-edge read-back differs from the plan.");
                if (live.GetDisplayTangentEdges2() != TangentEdgeValue(spec.TangentEdges))
                    errors.Add("View '" + spec.Id + "' tangent-edge read-back differs from the plan.");
                if (!string.IsNullOrEmpty(spec.DisplayState) &&
                    !string.Equals(live.DisplayState, spec.DisplayState, StringComparison.OrdinalIgnoreCase))
                    errors.Add("View '" + spec.Id + "' display state read-back differs from the plan.");
                if (live.PositionLocked != spec.LockPosition)
                    errors.Add("View '" + spec.Id + "' position lock read-back differs from the plan.");

                if (spec.Kind == "projected")
                {
                    IView expectedParent;
                    if (!matched.TryGetValue(spec.ParentId, out expectedParent) || baseView == null ||
                        !string.Equals(SafeViewUniqueName(baseView), SafeViewUniqueName(expectedParent),
                            StringComparison.Ordinal))
                        errors.Add("Projected view '" + spec.Id + "' does not retain parent '" +
                            spec.ParentId + "'.");
                    if (live.Type != 4)
                        errors.Add("Projected view '" + spec.Id + "' has unexpected view type " + live.Type + ".");
                }

                if (outline == null || outline.Length < 4)
                    errors.Add("View '" + spec.Id + "' has no readable outline.");
                else
                {
                    outlines[spec.Id] = outline;
                    if (outline[0] < plan.SheetMargin - plan.PositionTolerance ||
                        outline[1] < plan.SheetMargin - plan.PositionTolerance ||
                        outline[2] > sheetWidth - plan.SheetMargin + plan.PositionTolerance ||
                        outline[3] > sheetHeight - plan.SheetMargin + plan.PositionTolerance)
                        errors.Add("View '" + spec.Id + "' crosses the configured sheet margin.");
                }
            }

            if (plan.RequireNoOverlap)
            {
                for (int i = 0; i < plan.Views.Count; i++)
                {
                    double[] a;
                    if (!outlines.TryGetValue(plan.Views[i].Id, out a)) continue;
                    for (int j = i + 1; j < plan.Views.Count; j++)
                    {
                        double[] b;
                        if (!outlines.TryGetValue(plan.Views[j].Id, out b)) continue;
                        bool separated = a[2] + plan.ViewClearance <= b[0] ||
                            b[2] + plan.ViewClearance <= a[0] ||
                            a[3] + plan.ViewClearance <= b[1] ||
                            b[3] + plan.ViewClearance <= a[1];
                        if (!separated)
                            errors.Add("Views '" + plan.Views[i].Id + "' and '" +
                                plan.Views[j].Id + "' overlap or violate view_clearance_m.");
                    }
                }
            }

            snapshot["error_count"] = errors.Count;
            snapshot["errors"] = JArray.FromObject(errors);
            snapshot["verified"] = errors.Count == 0;
            return errors.Count == 0;
        }

        private OpenModelScope OpenModelForDrawing(string modelPath, string requestedConfiguration,
            out string error)
        {
            error = null;
            IModelDoc2 document = _solidWorks.GetOpenDocumentByName(modelPath) as IModelDoc2;
            bool openedByExecutor = false;
            if (document == null)
            {
                int openErrors = 0;
                int openWarnings = 0;
                document = _solidWorks.OpenDoc6(modelPath, 1, 3, "",
                    ref openErrors, ref openWarnings) as IModelDoc2;
                openedByExecutor = document != null;
                if (document == null)
                {
                    error = "OpenDoc6 failed for the model (errors=" + openErrors +
                        ", warnings=" + openWarnings + ").";
                    return null;
                }
            }

            if (!(document is IPartDoc))
            {
                if (openedByExecutor) _solidWorks.CloseDoc(document.GetTitle());
                error = "Only .SLDPRT source models are supported by drawing-plan schema 1.0.";
                return null;
            }

            string configuration = requestedConfiguration;
            if (string.IsNullOrWhiteSpace(configuration))
            {
                var active = document.ConfigurationManager != null
                    ? document.ConfigurationManager.ActiveConfiguration : null;
                configuration = active != null ? active.Name : null;
            }
            if (string.IsNullOrEmpty(configuration) || document.GetConfigurationByName(configuration) == null)
            {
                if (openedByExecutor) _solidWorks.CloseDoc(document.GetTitle());
                error = "Configuration '" + configuration + "' does not exist in the source model.";
                return null;
            }

            Dictionary<string, string> standardNames;
            string namesError;
            if (!TryResolveStandardViewNames(document, out standardNames, out namesError))
            {
                if (openedByExecutor) _solidWorks.CloseDoc(document.GetTitle());
                error = namesError;
                return null;
            }
            return new OpenModelScope
            {
                Document = document,
                OpenedByExecutor = openedByExecutor,
                Configuration = configuration,
                StandardViewNames = standardNames
            };
        }

        private static bool TryResolveStandardViewNames(IModelDoc2 model,
            out Dictionary<string, string> names, out string error)
        {
            names = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            error = null;
            var raw = model.GetModelViewNames() as Array;
            // SolidWorks returns standard names in the swStandardViews_e order, preceded by
            // *NormalTo. Using the returned strings keeps the executor locale-independent.
            if (raw == null || raw.Length < 10)
            {
                error = "GetModelViewNames did not return the nine standard view names.";
                return false;
            }
            string[] keys = { "front", "back", "left", "right", "top", "bottom", "isometric",
                "trimetric", "dimetric" };
            for (int i = 0; i < keys.Length; i++)
            {
                string actual = Convert.ToString(raw.GetValue(i + 1), CultureInfo.InvariantCulture);
                if (string.IsNullOrWhiteSpace(actual))
                {
                    error = "A standard model view name could not be resolved for orientation '" +
                        keys[i] + "'.";
                    return false;
                }
                names[keys[i]] = actual;
            }
            return true;
        }

        private static bool ConfigureSheet(ISheet sheet, DrawingPlanSpec plan,
            out double width, out double height, out string error)
        {
            width = 0.0;
            height = 0.0;
            error = null;
            try
            {
                var properties = sheet.GetProperties2() as double[];
                if (properties == null || properties.Length < 8)
                {
                    error = "GetProperties2 returned an unexpected sheet property array.";
                    return false;
                }
                bool firstAngle = properties[4] != 0.0;
                if (plan.Projection == "first_angle") firstAngle = true;
                if (plan.Projection == "third_angle") firstAngle = false;
                sheet.SetProperties2((int)properties[0], (int)properties[1],
                    plan.SheetScaleNumerator, plan.SheetScaleDenominator, firstAngle,
                    properties[5], properties[6], properties[7] != 0.0);
                return TryReadSheetSize(sheet, out width, out height, out error);
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
        }

        private static bool TryReadSheetSize(ISheet sheet, out double width,
            out double height, out string error)
        {
            width = 0.0;
            height = 0.0;
            error = null;
            if (sheet == null)
            {
                error = "No active sheet is available.";
                return false;
            }
            var properties = sheet.GetProperties2() as double[];
            if (properties == null || properties.Length < 7 ||
                properties[5] <= 0.0 || properties[6] <= 0.0)
            {
                error = "The active sheet has invalid width/height properties.";
                return false;
            }
            width = properties[5];
            height = properties[6];
            return true;
        }

        private bool TryParseDrawingPlan(JObject requestParams, out DrawingPlanSpec plan,
            out string errorCode, out string errorMessage)
        {
            plan = null;
            errorCode = "INVALID_DRAWING_PLAN";
            errorMessage = null;
            if (requestParams == null)
            {
                errorMessage = "params must be an object containing plan.";
                return false;
            }
            string unknown = FindUnknownProperty(requestParams, "plan");
            if (unknown != null)
            {
                errorMessage = "Unknown request parameter '" + unknown + "'.";
                return false;
            }

            JToken planToken = requestParams["plan"];
            JObject root = planToken as JObject;
            string suppliedPlanText = null;
            if (root == null && planToken != null && planToken.Type == JTokenType.String)
            {
                suppliedPlanText = planToken.Value<string>();
                try { root = JObject.Parse(suppliedPlanText); }
                catch (JsonException ex)
                {
                    errorMessage = "plan is not valid JSON: " + ex.Message;
                    return false;
                }
            }
            if (root == null)
            {
                errorMessage = "plan must be a JSON object.";
                return false;
            }
            unknown = FindUnknownProperty(root, "schema_version", "model", "drawing",
                "sheet", "views", "verification");
            if (unknown != null)
            {
                errorMessage = "Unknown plan property '" + unknown + "'.";
                return false;
            }
            if (root.Value<string>("schema_version") != DrawingPlanSchemaVersion)
            {
                errorMessage = "schema_version must be exactly '" + DrawingPlanSchemaVersion + "'.";
                return false;
            }

            var model = root["model"] as JObject;
            var drawing = root["drawing"] as JObject;
            var sheet = root["sheet"] as JObject;
            var views = root["views"] as JArray;
            var verification = root["verification"] as JObject;
            if (model == null || drawing == null || sheet == null || views == null)
            {
                errorMessage = "model, drawing, sheet, and views are required with object/array types.";
                return false;
            }
            unknown = FindUnknownProperty(model, "path", "configuration", "display_state");
            if (unknown != null) { errorMessage = "Unknown model property '" + unknown + "'."; return false; }
            unknown = FindUnknownProperty(drawing, "template_path", "output_path", "overwrite", "projection");
            if (unknown != null) { errorMessage = "Unknown drawing property '" + unknown + "'."; return false; }
            unknown = FindUnknownProperty(sheet, "scale_numerator", "scale_denominator",
                "margin_m", "view_clearance_m", "require_no_overlap");
            if (unknown != null) { errorMessage = "Unknown sheet property '" + unknown + "'."; return false; }
            if (verification != null)
            {
                unknown = FindUnknownProperty(verification, "position_tolerance_m", "scale_tolerance");
                if (unknown != null) { errorMessage = "Unknown verification property '" + unknown + "'."; return false; }
            }

            string modelPath;
            string templatePath;
            string pathError;
            if (!TryValidateExistingAbsolutePath(model.Value<string>("path"),
                new[] { ".SLDPRT" }, out modelPath, out pathError))
            { errorCode = "INVALID_MODEL_PATH"; errorMessage = pathError; return false; }
            if (!TryValidateExistingAbsolutePath(drawing.Value<string>("template_path"),
                new[] { ".DRWDOT" }, out templatePath, out pathError))
            { errorCode = "INVALID_TEMPLATE_PATH"; errorMessage = pathError; return false; }

            string rawOutput = drawing.Value<string>("output_path");
            if (string.IsNullOrWhiteSpace(rawOutput) || !Path.IsPathRooted(rawOutput))
            { errorCode = "INVALID_OUTPUT_PATH"; errorMessage = "drawing.output_path must be an absolute .SLDDRW path."; return false; }
            string outputPath;
            try { outputPath = Path.GetFullPath(rawOutput); }
            catch (Exception ex) { errorCode = "INVALID_OUTPUT_PATH"; errorMessage = ex.Message; return false; }
            if (!string.Equals(Path.GetExtension(outputPath), ".SLDDRW", StringComparison.OrdinalIgnoreCase))
            { errorCode = "INVALID_OUTPUT_PATH"; errorMessage = "drawing.output_path must end with .SLDDRW."; return false; }
            string parent = Path.GetDirectoryName(outputPath);
            if (string.IsNullOrEmpty(parent) || !Directory.Exists(parent))
            { errorCode = "OUTPUT_DIRECTORY_NOT_FOUND"; errorMessage = "The output directory must already exist: " + parent; return false; }
            if (string.Equals(outputPath, modelPath, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(outputPath, templatePath, StringComparison.OrdinalIgnoreCase))
            { errorCode = "INVALID_OUTPUT_PATH"; errorMessage = "The output path must differ from every input path."; return false; }

            string projection = (drawing.Value<string>("projection") ?? "preserve").ToLowerInvariant();
            if (!new[] { "preserve", "first_angle", "third_angle" }.Contains(projection))
            { errorMessage = "drawing.projection must be preserve, first_angle, or third_angle."; return false; }

            double numerator = sheet.Value<double?>("scale_numerator") ?? 1.0;
            double denominator = sheet.Value<double?>("scale_denominator") ?? 1.0;
            double margin = sheet.Value<double?>("margin_m") ?? 0.005;
            double clearance = sheet.Value<double?>("view_clearance_m") ?? 0.003;
            if (!IsFinitePositive(numerator) || !IsFinitePositive(denominator) ||
                numerator / denominator < 0.001 || numerator / denominator > 100.0)
            { errorMessage = "The sheet scale must be finite and between 0.001 and 100."; return false; }
            if (!IsFiniteInRange(margin, 0.0, 0.05) || !IsFiniteInRange(clearance, 0.0, 0.05))
            { errorMessage = "sheet.margin_m and sheet.view_clearance_m must be between 0 and 0.05 m."; return false; }

            double positionTolerance = verification != null
                ? verification.Value<double?>("position_tolerance_m") ?? 0.0005 : 0.0005;
            double scaleTolerance = verification != null
                ? verification.Value<double?>("scale_tolerance") ?? 0.000001 : 0.000001;
            if (!IsFiniteInRange(positionTolerance, 0.000001, 0.005) ||
                !IsFiniteInRange(scaleTolerance, 0.000000001, 0.001))
            { errorMessage = "Verification tolerances are outside their allowed ranges."; return false; }

            if (views.Count < 1 || views.Count > 16)
            { errorMessage = "views must contain between 1 and 16 view specifications."; return false; }
            var parsedViews = new List<DrawingViewPlanSpec>();
            var ids = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < views.Count; i++)
            {
                var item = views[i] as JObject;
                if (item == null) { errorMessage = "views[" + i + "] must be an object."; return false; }
                unknown = FindUnknownProperty(item, "id", "kind", "orientation", "parent_id",
                    "position", "scale_mode", "scale", "display_mode", "tangent_edges",
                    "configuration", "display_state", "lock_position");
                if (unknown != null) { errorMessage = "Unknown views[" + i + "] property '" + unknown + "'."; return false; }
                string id = item.Value<string>("id");
                string kind = (item.Value<string>("kind") ?? "").ToLowerInvariant();
                if (string.IsNullOrEmpty(id) || !DrawingViewIdPattern.IsMatch(id) || !ids.Add(id))
                { errorMessage = "Each view id must be unique and match " + DrawingViewIdPattern + "."; return false; }
                if (kind != "base" && kind != "projected")
                { errorMessage = "views[" + i + "].kind must be base or projected."; return false; }
                var position = item["position"] as JObject;
                if (position == null || FindUnknownProperty(position, "x", "y") != null ||
                    position["x"] == null || position["y"] == null)
                { errorMessage = "views[" + i + "].position must contain only numeric x and y."; return false; }
                double x;
                double y;
                try { x = position.Value<double>("x"); y = position.Value<double>("y"); }
                catch { errorMessage = "views[" + i + "].position x/y must be numbers."; return false; }
                if (!IsFiniteInRange(x, 0.000001, 2.0) || !IsFiniteInRange(y, 0.000001, 2.0))
                { errorMessage = "View positions must be finite positive meters no greater than 2."; return false; }

                string orientation = (item.Value<string>("orientation") ?? "").ToLowerInvariant();
                string parentId = item.Value<string>("parent_id");
                if (kind == "base")
                {
                    if (!new[] { "front", "back", "left", "right", "top", "bottom", "isometric" }.Contains(orientation))
                    { errorMessage = "Base view '" + id + "' has an unsupported orientation."; return false; }
                    if (!string.IsNullOrEmpty(parentId))
                    { errorMessage = "Base view '" + id + "' must not define parent_id."; return false; }
                }
                else
                {
                    if (string.IsNullOrEmpty(parentId) || !ids.Contains(parentId))
                    { errorMessage = "Projected view '" + id + "' must reference a preceding parent_id."; return false; }
                    if (item["configuration"] != null || item["display_state"] != null)
                    { errorMessage = "Projected view '" + id + "' must inherit configuration/display_state from its parent."; return false; }
                    var parentView = parsedViews.First(v => v.Id == parentId);
                    double dx = Math.Abs(x - parentView.X);
                    double dy = Math.Abs(y - parentView.Y);
                    if ((dx <= positionTolerance && dy <= positionTolerance) ||
                        (dx > positionTolerance && dy > positionTolerance))
                    { errorMessage = "Projected view '" + id + "' must be horizontally or vertically aligned with its parent."; return false; }
                }

                string scaleMode = (item.Value<string>("scale_mode") ??
                    (kind == "projected" ? "parent" : "sheet")).ToLowerInvariant();
                if (!new[] { "sheet", "custom", "parent" }.Contains(scaleMode) ||
                    (kind == "base" && scaleMode == "parent"))
                { errorMessage = "View '" + id + "' has an invalid scale_mode."; return false; }
                double? customScale = item.Value<double?>("scale");
                if (scaleMode == "custom" && (customScale == null ||
                    !IsFiniteInRange(customScale.Value, 0.001, 100.0)))
                { errorMessage = "View '" + id + "' requires a finite custom scale between 0.001 and 100."; return false; }
                if (scaleMode != "custom" && customScale != null)
                { errorMessage = "View '" + id + "' must omit scale unless scale_mode is custom."; return false; }

                string displayMode = (item.Value<string>("display_mode") ?? "hidden_lines_removed").ToLowerInvariant();
                if (!new[] { "wireframe", "hidden_lines_removed", "hidden_lines_visible", "shaded", "shaded_with_edges" }.Contains(displayMode))
                { errorMessage = "View '" + id + "' has an invalid display_mode."; return false; }
                string tangentEdges = (item.Value<string>("tangent_edges") ?? "removed").ToLowerInvariant();
                if (!new[] { "removed", "fonted", "visible" }.Contains(tangentEdges))
                { errorMessage = "View '" + id + "' has an invalid tangent_edges value."; return false; }

                parsedViews.Add(new DrawingViewPlanSpec
                {
                    Id = id,
                    Kind = kind,
                    Orientation = orientation,
                    ParentId = parentId,
                    X = x,
                    Y = y,
                    ScaleMode = scaleMode,
                    Scale = customScale,
                    DisplayMode = displayMode,
                    TangentEdges = tangentEdges,
                    Configuration = item.Value<string>("configuration"),
                    DisplayState = item.Value<string>("display_state"),
                    LockPosition = item.Value<bool?>("lock_position") ?? true
                });
            }

            var canonical = CanonicalizeToken(root) as JObject;
            plan = new DrawingPlanSpec
            {
                ModelPath = modelPath,
                Configuration = model.Value<string>("configuration") ?? "",
                DisplayState = model.Value<string>("display_state") ?? "",
                TemplatePath = templatePath,
                OutputPath = outputPath,
                Overwrite = drawing.Value<bool?>("overwrite") ?? false,
                Projection = projection,
                SheetScaleNumerator = numerator,
                SheetScaleDenominator = denominator,
                SheetMargin = margin,
                ViewClearance = clearance,
                RequireNoOverlap = sheet.Value<bool?>("require_no_overlap") ?? true,
                PositionTolerance = positionTolerance,
                ScaleTolerance = scaleTolerance,
                Views = parsedViews,
                CanonicalPlan = canonical,
                PlanSha256 = ComputeTextSha256(suppliedPlanText ?? canonical.ToString(Formatting.None))
            };
            // Resolve the active configuration during model preflight, but verification needs the exact
            // value. It is filled by the transaction before snapshots are built.
            return true;
        }

        private static int DisplayModeValue(string value)
        {
            switch (value)
            {
                // SetDisplayMode3/GetDisplayMode2 use swDisplayMode_e (not the similarly
                // named swViewDisplayMode_e). Values reflected from the installed 2025 interop.
                case "wireframe": return 0;
                case "hidden_lines_removed": return 2;
                case "hidden_lines_visible": return 1;
                case "shaded": return 3;
                case "shaded_with_edges": return 7;
                default: throw new ArgumentOutOfRangeException("value");
            }
        }

        private static int TangentEdgeValue(string value)
        {
            switch (value)
            {
                case "removed": return 0;
                case "fonted": return 1;
                case "visible": return 2;
                default: throw new ArgumentOutOfRangeException("value");
            }
        }

        private static string SafeViewUniqueName(IView view)
        {
            try { return view != null ? view.GetUniqueName() : null; }
            catch { return null; }
        }

        private static string FindUnknownProperty(JObject obj, params string[] allowed)
        {
            if (obj == null) return null;
            var set = new HashSet<string>(allowed, StringComparer.Ordinal);
            var unknown = obj.Properties().FirstOrDefault(p => !set.Contains(p.Name));
            return unknown != null ? unknown.Name : null;
        }

        private static bool TryValidateExistingAbsolutePath(string raw, string[] extensions,
            out string fullPath, out string error)
        {
            fullPath = null;
            error = null;
            if (string.IsNullOrWhiteSpace(raw) || !Path.IsPathRooted(raw))
            { error = "The path must be absolute."; return false; }
            try { fullPath = Path.GetFullPath(raw); }
            catch (Exception ex) { error = ex.Message; return false; }
            string actualExtension = Path.GetExtension(fullPath);
            if (!extensions.Any(ext => string.Equals(actualExtension, ext,
                StringComparison.OrdinalIgnoreCase)))
            { error = "The path must use one of these extensions: " + string.Join(", ", extensions) + "."; return false; }
            if (!File.Exists(fullPath))
            { error = "The file does not exist: " + fullPath; return false; }
            return true;
        }

        private static bool IsFinitePositive(double value)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value) && value > 0.0;
        }

        private static bool IsFiniteInRange(double value, double minimum, double maximum)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value) &&
                value >= minimum && value <= maximum;
        }

        private static JToken CanonicalizeToken(JToken token)
        {
            var obj = token as JObject;
            if (obj != null)
            {
                var sorted = new JObject();
                foreach (var property in obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal))
                    sorted[property.Name] = CanonicalizeToken(property.Value);
                return sorted;
            }
            var array = token as JArray;
            if (array != null)
                return new JArray(array.Select(CanonicalizeToken));
            return token.DeepClone();
        }

        private static string ComputeTextSha256(string text)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text)))
                    .Replace("-", "").ToLowerInvariant();
        }

        private static string ComputeFileSha256(string path)
        {
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static bool TryLoadVerificationReport(DrawingPlanSpec plan,
            out Dictionary<string, string> handles, out string artifactHash, out string error)
        {
            handles = new Dictionary<string, string>(StringComparer.Ordinal);
            artifactHash = null;
            error = null;
            string reportPath = plan.OutputPath + ".verification.json";
            if (!File.Exists(reportPath))
            {
                error = "The required verification report does not exist: " + reportPath;
                return false;
            }
            try
            {
                var report = JObject.Parse(File.ReadAllText(reportPath, Encoding.UTF8));
                if (report.Value<string>("schema_version") != "1.0" ||
                    report.Value<bool?>("verified") != true)
                {
                    error = "The verification report schema/status is invalid.";
                    return false;
                }
                if (!string.Equals(report.Value<string>("plan_sha256"), plan.PlanSha256,
                    StringComparison.OrdinalIgnoreCase))
                {
                    error = "The supplied plan SHA-256 does not match the verification report.";
                    return false;
                }
                artifactHash = report.Value<string>("artifact_sha256");
                if (string.IsNullOrEmpty(artifactHash))
                {
                    error = "The verification report has no artifact_sha256.";
                    return false;
                }
                var handleObject = report["view_handles"] as JObject;
                if (handleObject == null)
                {
                    error = "The verification report has no view_handles map.";
                    return false;
                }
                foreach (var property in handleObject.Properties())
                {
                    string value = property.Value.Value<string>();
                    if (string.IsNullOrEmpty(value))
                    {
                        error = "The verification report contains an empty view handle.";
                        return false;
                    }
                    handles[property.Name] = value;
                }
                foreach (var plannedView in plan.Views)
                {
                    if (!handles.ContainsKey(plannedView.Id))
                    {
                        error = "The verification report does not contain every planned view handle.";
                        return false;
                    }
                }
                return true;
            }
            catch (Exception ex)
            {
                error = "Could not read the verification report: " + ex.Message;
                return false;
            }
        }

        private static void CommitGeneratedFile(string tempPath, string finalPath, bool overwrite)
        {
            if (!File.Exists(tempPath))
                throw new FileNotFoundException("Generated transaction file is missing.", tempPath);
            if (!File.Exists(finalPath))
            {
                File.Move(tempPath, finalPath);
                return;
            }
            if (!overwrite)
                throw new IOException("The final path exists and overwrite is false: " + finalPath);
            string backup = finalPath + ".q3ds-backup." + Guid.NewGuid().ToString("N");
            File.Replace(tempPath, finalPath, backup, true);
            DeleteGeneratedFileIfPresent(backup);
        }

        private static void DeleteGeneratedFileIfPresent(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;
            File.Delete(path);
        }

        private void CloseModelScope(OpenModelScope scope)
        {
            if (scope == null || scope.Document == null || !scope.OpenedByExecutor) return;
            try { _solidWorks.CloseDoc(scope.Document.GetTitle()); } catch { }
        }

        private string GetActiveDocumentTitle()
        {
            try
            {
                var active = _solidWorks != null ? _solidWorks.IActiveDoc2 as IModelDoc2 : null;
                return active != null ? active.GetTitle() : null;
            }
            catch { return null; }
        }

        private void RestoreActiveDocument(string title)
        {
            if (string.IsNullOrEmpty(title) || _solidWorks == null) return;
            try
            {
                int errors = 0;
                _solidWorks.ActivateDoc3(title, false, 0, ref errors);
            }
            catch { }
        }

        private CadState BuildCurrentCadState(int stateVersion)
        {
            IModelDoc2 active = null;
            try { active = _solidWorks != null ? _solidWorks.IActiveDoc2 as IModelDoc2 : null; }
            catch { }
            return new CadState
            {
                StateVersion = stateVersion,
                ActiveDocument = active != null ? active.GetTitle() : null,
                DocumentType = active != null ? DocTypeName(active) : null,
                ActiveSketch = null,
                Features = new List<string>(),
                Dimensions = new List<string>()
            };
        }

        private ExecutionResponse BuildDrawingFailure(string operationId, string code,
            string message, JObject diagnostics)
        {
            return new ExecutionResponse
            {
                OperationId = operationId,
                Status = "FAILED",
                Verified = false,
                StateVersion = _guard.GetCurrentStateVersion(),
                Error = new ExecutionError { Code = code, Message = message },
                ResultGeometry = diagnostics
            };
        }
    }
}
