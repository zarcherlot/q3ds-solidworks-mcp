using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Repository-owned initializer for the frozen drawing-planning handoff. SolidWorks COM,
    /// source-document restoration, screenshot generation, blank-drawing persistence and the
    /// manifest-last disk transaction all stay inside the C# execution boundary.
    /// </summary>
    public partial class SolidWorksService
    {
        private static readonly string[] InitializerStandardViews =
            { "front", "back", "left", "right", "top", "bottom" };

        public ExecutionResponse InitializePartDrawingHandoff(ToolRequest request)
        {
            if (_guard.IsDuplicate(request.OperationId))
                return _guard.GetDuplicate(request.OperationId);
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");

            var parameters = request.Params as JObject;
            string modelPath;
            string templatePath;
            string pathError;
            if (!TryValidateExistingAbsolutePath(parameters != null
                    ? parameters.Value<string>("model_path") : null,
                new[] { ".SLDPRT" }, out modelPath, out pathError))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_MODEL_PATH", pathError);
            if (!TryValidateExistingAbsolutePath(parameters != null
                    ? parameters.Value<string>("drawing_template_path") : null,
                new[] { ".DRWDOT" }, out templatePath, out pathError))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_DRAWING_TEMPLATE_PATH", pathError);

            string publicationDirectory;
            if (!TryValidateInitializerDirectory(parameters != null
                    ? parameters.Value<string>("publication_directory") : null,
                out publicationDirectory, out pathError))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_PUBLICATION_DIRECTORY", pathError);

            int imageWidth = parameters != null
                ? parameters.Value<int?>("image_width") ?? 1024 : 1024;
            int imageHeight = parameters != null
                ? parameters.Value<int?>("image_height") ?? 768 : 768;
            if (imageWidth < 320 || imageWidth > 2000 || imageHeight < 240 || imageHeight > 2000)
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_IMAGE_SIZE", "image_width must be 320..2000 and image_height 240..2000.");

            var finalPaths = InitializerFinalPaths(publicationDirectory);
            string collision = finalPaths.Values.FirstOrDefault(File.Exists);
            if (collision != null)
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INITIALIZER_OUTPUT_EXISTS", "Initializer output already exists: " + collision);

            string previousTitle = GetActiveDocumentTitle();
            string sourceHashBefore;
            try { sourceHashBefore = ComputeFileSha256(modelPath); }
            catch (Exception ex)
            {
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "MODEL_HASH_FAILED", ex.Message);
            }

            string stagingDirectory = Path.Combine(publicationDirectory,
                ".q3ds-initializer-" + Guid.NewGuid().ToString("N"));
            var moved = new List<string>();
            OpenModelScope modelScope = null;
            bool completed = false;
            try
            {
                Directory.CreateDirectory(stagingDirectory);
                string openError;
                modelScope = OpenModelForDrawing(modelPath, "", out openError);
                if (modelScope == null)
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "MODEL_OPEN_FAILED", openError);
                if (modelScope.Document.GetSaveFlag())
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "MODEL_HAS_UNSAVED_CHANGES",
                        "The source model has unsaved changes. Save it before initialization.");

                int activationErrors = 0;
                _solidWorks.ActivateDoc3(modelScope.Document.GetTitle(), false, 0,
                    ref activationErrors);
                if (!ReferenceEquals(_solidWorks.IActiveDoc2, modelScope.Document) &&
                    !string.Equals(GetActiveDocumentTitle(), modelScope.Document.GetTitle(),
                        StringComparison.OrdinalIgnoreCase))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "MODEL_ACTIVATION_FAILED", "The source model could not be made active.");

                string displayState = FirstDisplayState(modelScope.Document,
                    modelScope.Configuration);
                if (string.IsNullOrWhiteSpace(displayState))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "DISPLAY_STATE_UNAVAILABLE",
                        "The active configuration has no readable display state.");

                var geometryReport = BuildInitializerGeometryReport(modelScope.Document,
                    modelPath, modelScope.Configuration, displayState);
                int bodyCount = geometryReport["bodies"] is JArray
                    ? ((JArray)geometryReport["bodies"]).Count : 0;
                if (bodyCount == 0)
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "NO_SOLID_BODIES", "The source part contains no resolved solid body.");

                double[] originalOrientation = SnapshotOrientation(modelScope.Document);
                bool dirtyBeforeCapture = modelScope.Document.GetSaveFlag();
                try
                {
                    foreach (string view in InitializerStandardViews)
                    {
                        string finalPath = finalPaths["image:" + view];
                        string stagedPath = Path.Combine(stagingDirectory,
                            Path.GetFileName(finalPath));
                        string captureError;
                        if (!TryCaptureInitializerView(modelScope.Document,
                            modelScope.StandardViewNames[view], stagedPath,
                            imageWidth, imageHeight, out captureError))
                            return BuildFailed(request.OperationId,
                                _guard.GetCurrentStateVersion(), "STANDARD_VIEW_CAPTURE_FAILED",
                                view + ": " + captureError);
                    }
                }
                finally
                {
                    RestoreOrientation(modelScope.Document, originalOrientation);
                }
                if (modelScope.Document.GetSaveFlag() != dirtyBeforeCapture)
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "SOURCE_MODEL_DIRTY_STATE_CHANGED",
                        "Standard-view capture changed the source model dirty state.");

                JObject drawingContext;
                string stagedBlank = Path.Combine(stagingDirectory,
                    Path.GetFileName(finalPaths["blank_drawing"]));
                string blankError;
                if (!TryCreateAndVerifyBlankDrawing(templatePath, stagedBlank,
                    out drawingContext, out blankError))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "BLANK_DRAWING_INITIALIZATION_FAILED", blankError);

                string stagedGeometry = Path.Combine(stagingDirectory,
                    Path.GetFileName(finalPaths["geometry_report"]));
                File.WriteAllText(stagedGeometry,
                    geometryReport.ToString(Formatting.Indented) + System.Environment.NewLine,
                    new UTF8Encoding(false));

                var readinessReport = BuildInitializerReadinessReport(modelPath,
                    sourceHashBefore, templatePath, modelScope.Configuration, displayState,
                    imageWidth, imageHeight, drawingContext, finalPaths, geometryReport);
                string stagedReadiness = Path.Combine(stagingDirectory,
                    Path.GetFileName(finalPaths["readiness_report"]));
                File.WriteAllText(stagedReadiness,
                    readinessReport.ToString(Formatting.Indented) + System.Environment.NewLine,
                    new UTF8Encoding(false));

                string sourceHashAfter = ComputeFileSha256(modelPath);
                if (!string.Equals(sourceHashBefore, sourceHashAfter,
                    StringComparison.OrdinalIgnoreCase))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "MODEL_HASH_CHANGED",
                        "The source model changed while the initializer transaction was running.");

                var artifactHashes = new Dictionary<string, string>(StringComparer.Ordinal);
                artifactHashes["model"] = sourceHashAfter;
                artifactHashes["blank_drawing"] = ComputeFileSha256(stagedBlank);
                artifactHashes["readiness_report"] = ComputeFileSha256(stagedReadiness);
                artifactHashes["geometry_report"] = ComputeFileSha256(stagedGeometry);
                foreach (string view in InitializerStandardViews)
                    artifactHashes["image:" + view] = ComputeFileSha256(Path.Combine(
                        stagingDirectory, Path.GetFileName(finalPaths["image:" + view])));

                var imageRows = new JArray();
                foreach (string view in InitializerStandardViews)
                    imageRows.Add(new JObject
                    {
                        ["view"] = view,
                        ["path"] = finalPaths["image:" + view],
                        ["sha256"] = artifactHashes["image:" + view]
                    });
                string handoffId = "DH-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss",
                    CultureInfo.InvariantCulture) + "-" + sourceHashAfter.Substring(0, 12);
                var manifest = new JObject
                {
                    ["protocol_id"] = "q3ds-drawing-planning-handoff",
                    ["schema_version"] = "1.0",
                    ["handoff_id"] = handoffId,
                    ["status"] = "ready",
                    ["model"] = new JObject
                    {
                        ["path"] = modelPath,
                        ["sha256"] = sourceHashAfter,
                        ["configuration"] = modelScope.Configuration,
                        ["display_state"] = displayState
                    },
                    ["blank_drawing"] = new JObject
                    {
                        ["path"] = finalPaths["blank_drawing"],
                        ["sha256"] = artifactHashes["blank_drawing"],
                        ["blank"] = true
                    },
                    ["readiness_report"] = new JObject
                    {
                        ["path"] = finalPaths["readiness_report"],
                        ["sha256"] = artifactHashes["readiness_report"]
                    },
                    ["geometry_report"] = new JObject
                    {
                        ["path"] = finalPaths["geometry_report"],
                        ["sha256"] = artifactHashes["geometry_report"]
                    },
                    ["standard_view_images"] = imageRows,
                    ["drawing_context"] = drawingContext,
                    ["blocking_issues"] = new JArray(),
                    ["open_questions"] = new JArray()
                };
                string stagedManifest = Path.Combine(stagingDirectory,
                    Path.GetFileName(finalPaths["manifest"]));
                File.WriteAllText(stagedManifest,
                    manifest.ToString(Formatting.Indented) + System.Environment.NewLine,
                    new UTF8Encoding(false));

                foreach (string key in new[] { "blank_drawing", "readiness_report",
                    "geometry_report", "image:front", "image:back", "image:left",
                    "image:right", "image:top", "image:bottom" })
                {
                    string finalPath = finalPaths[key];
                    if (File.Exists(finalPath))
                        return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                            "INITIALIZER_OUTPUT_RACE", "An output path appeared before commit: " + finalPath);
                    File.Move(Path.Combine(stagingDirectory, Path.GetFileName(finalPath)), finalPath);
                    moved.Add(finalPath);
                }
                if (File.Exists(finalPaths["manifest"]))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "INITIALIZER_OUTPUT_RACE",
                        "The handoff manifest path appeared before commit: " + finalPaths["manifest"]);
                File.Move(stagedManifest, finalPaths["manifest"]);
                moved.Add(finalPaths["manifest"]);

                string manifestHash = ComputeFileSha256(finalPaths["manifest"]);
                completed = true;
                int nextState = _guard.GetCurrentStateVersion() + 1;
                var response = new ExecutionResponse
                {
                    OperationId = request.OperationId,
                    Status = "COMPLETED",
                    Verified = true,
                    StateVersion = nextState,
                    CadState = BuildCurrentCadState(nextState),
                    ResultGeometry = new JObject
                    {
                        ["kind"] = "drawing_planning_handoff",
                        ["handoff_id"] = handoffId,
                        ["manifest_path"] = finalPaths["manifest"],
                        ["manifest_sha256"] = manifestHash,
                        ["publication_directory"] = publicationDirectory,
                        ["blank_drawing_path"] = finalPaths["blank_drawing"],
                        ["readiness_report_path"] = finalPaths["readiness_report"],
                        ["geometry_report_path"] = finalPaths["geometry_report"],
                        ["standard_view_images"] = imageRows,
                        ["configuration"] = modelScope.Configuration,
                        ["display_state"] = displayState,
                        ["drawing_context"] = drawingContext,
                        ["source_model_read_only"] = true,
                        ["verified"] = true
                    }
                };
                if (response.CadState != null)
                    response.CadState.Features = new List<string> { finalPaths["manifest"] };
                _guard.RegisterCompleted(request.OperationId, response);
                return response;
            }
            catch (Exception ex)
            {
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "DRAWING_HANDOFF_INITIALIZATION_FAILED", ex.Message);
            }
            finally
            {
                CloseModelScope(modelScope);
                RestoreActiveDocument(previousTitle);
                if (!completed)
                {
                    foreach (string path in moved)
                    {
                        try { if (File.Exists(path)) File.Delete(path); } catch { }
                    }
                }
                try
                {
                    if (Directory.Exists(stagingDirectory))
                        Directory.Delete(stagingDirectory, true);
                }
                catch { }
            }
        }

        private static bool TryValidateInitializerDirectory(string raw, out string fullPath,
            out string error)
        {
            fullPath = null;
            error = null;
            if (string.IsNullOrWhiteSpace(raw) || !Path.IsPathRooted(raw))
            { error = "The publication directory must be absolute."; return false; }
            if (raw.IndexOfAny(new[] { '*', '?', '[', ']' }) >= 0)
            { error = "The publication directory must not contain wildcards."; return false; }
            try { fullPath = Path.GetFullPath(raw); }
            catch (Exception ex) { error = ex.Message; return false; }
            if (!Directory.Exists(fullPath))
            { error = "The publication directory does not exist: " + fullPath; return false; }
            return true;
        }

        private static Dictionary<string, string> InitializerFinalPaths(string directory)
        {
            var paths = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["manifest"] = Path.Combine(directory, "drawing-planning-handoff.json"),
                ["blank_drawing"] = Path.Combine(directory, "initializer-blank.SLDDRW"),
                ["readiness_report"] = Path.Combine(directory, "drawing-readiness.json"),
                ["geometry_report"] = Path.Combine(directory, "model-geometry.json")
            };
            foreach (string view in InitializerStandardViews)
                paths["image:" + view] = Path.Combine(directory, view + ".png");
            return paths;
        }

        private static string FirstDisplayState(IModelDoc2 model, string configuration)
        {
            try
            {
                var config = model.GetConfigurationByName(configuration) as IConfiguration;
                var states = config != null ? config.GetDisplayStates() as Array : null;
                if (states == null || states.Length == 0) return null;
                return Convert.ToString(states.GetValue(0), CultureInfo.InvariantCulture);
            }
            catch { return null; }
        }

        private static double[] SnapshotOrientation(IModelDoc2 model)
        {
            try
            {
                var view = model.IActiveView;
                var transform = view != null ? view.Orientation3 : null;
                var values = transform != null ? transform.ArrayData as double[] : null;
                return values != null ? (double[])values.Clone() : null;
            }
            catch { return null; }
        }

        private static void RestoreOrientation(IModelDoc2 model, double[] values)
        {
            if (values == null) return;
            try
            {
                var view = model.IActiveView;
                var transform = view != null ? view.Orientation3 : null;
                if (view == null || transform == null) return;
                transform.ArrayData = values;
                view.Orientation3 = transform;
                model.GraphicsRedraw2();
            }
            catch { }
        }

        private static bool TryCaptureInitializerView(IModelDoc2 model, string solidWorksViewName,
            string pngPath, int width, int height, out string error)
        {
            error = null;
            string bmpPath = Path.ChangeExtension(pngPath, ".capture.bmp");
            try
            {
                model.ShowNamedView2(solidWorksViewName, -1);
                model.ViewZoomtofit2();
                model.GraphicsRedraw2();
                if (!model.SaveBMP(bmpPath, width, height) || !File.Exists(bmpPath))
                { error = "SaveBMP returned false."; return false; }
                using (var bitmap = new System.Drawing.Bitmap(bmpPath))
                    bitmap.Save(pngPath, System.Drawing.Imaging.ImageFormat.Png);
                if (!File.Exists(pngPath) || new FileInfo(pngPath).Length == 0)
                { error = "PNG conversion did not create a non-empty file."; return false; }
                using (var verify = new System.Drawing.Bitmap(pngPath))
                {
                    if (verify.Width != width || verify.Height != height)
                    {
                        error = "PNG dimensions differ from the requested capture size.";
                        return false;
                    }
                }
                return true;
            }
            catch (Exception ex) { error = ex.Message; return false; }
            finally
            {
                try { if (File.Exists(bmpPath)) File.Delete(bmpPath); } catch { }
            }
        }

        private bool TryCreateAndVerifyBlankDrawing(string templatePath, string outputPath,
            out JObject context, out string error)
        {
            context = null;
            error = null;
            IModelDoc2 created = null;
            IModelDoc2 reopened = null;
            try
            {
                created = _solidWorks.NewDocument(templatePath, 0, 0.0, 0.0) as IModelDoc2;
                var drawing = created as IDrawingDoc;
                if (drawing == null)
                { error = "SolidWorks did not create a drawing from the supplied template."; return false; }
                if (!IsBlankDrawing(drawing))
                { error = "The supplied template created a drawing containing model views."; return false; }
                var sheet = drawing.GetCurrentSheet() as ISheet;
                if (sheet == null)
                { error = "The new drawing has no active sheet."; return false; }
                context = BuildDrawingContext(sheet, templatePath);
                created.ClearSelection2(true);
                int saveErrors = 0;
                int saveWarnings = 0;
                bool saved = created.Extension.SaveAs3(outputPath,
                    (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                    (int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                    null, null, ref saveErrors, ref saveWarnings);
                if (!saved || saveErrors != 0 || !File.Exists(outputPath))
                {
                    error = "SaveAs3 failed for initializer blank drawing (errors=" +
                        saveErrors + ", warnings=" + saveWarnings + ").";
                    return false;
                }
                string title = created.GetTitle();
                _solidWorks.CloseDoc(title);
                created = null;

                int reopenErrors = 0;
                int reopenWarnings = 0;
                reopened = _solidWorks.OpenDoc6(outputPath, 3, 3, "",
                    ref reopenErrors, ref reopenWarnings) as IModelDoc2;
                var reopenedDrawing = reopened as IDrawingDoc;
                if (reopenedDrawing == null || !reopened.IsOpenedReadOnly())
                {
                    error = "Read-only reopen failed for initializer blank drawing (errors=" +
                        reopenErrors + ", warnings=" + reopenWarnings + ").";
                    return false;
                }
                if (!IsBlankDrawing(reopenedDrawing))
                { error = "The reopened initializer drawing is not blank."; return false; }
                var reopenedSheet = reopenedDrawing.GetCurrentSheet() as ISheet;
                JObject reopenedContext = BuildDrawingContext(reopenedSheet, templatePath);
                if (!JToken.DeepEquals(context, reopenedContext))
                { error = "Sheet context changed after save and read-only reopen."; return false; }
                string reopenedTitle = reopened.GetTitle();
                _solidWorks.CloseDoc(reopenedTitle);
                reopened = null;
                return true;
            }
            catch (Exception ex) { error = ex.Message; return false; }
            finally
            {
                try { if (created != null) _solidWorks.CloseDoc(created.GetTitle()); } catch { }
                try { if (reopened != null) _solidWorks.CloseDoc(reopened.GetTitle()); } catch { }
            }
        }

        private static bool IsBlankDrawing(IDrawingDoc drawing)
        {
            if (drawing == null) return false;
            var sheetView = drawing.GetFirstView() as IView;
            return sheetView != null && sheetView.GetNextView() == null;
        }

        private static JObject BuildDrawingContext(ISheet sheet, string templatePath)
        {
            if (sheet == null) throw new InvalidDataException("No active drawing sheet is available.");
            var values = sheet.GetProperties2() as double[];
            if (values == null || values.Length < 7 || values[5] <= 0.0 || values[6] <= 0.0)
                throw new InvalidDataException("GetProperties2 returned invalid sheet properties.");
            double width = values[5];
            double height = values[6];
            double minimum = Math.Min(width, height);
            double frameMargin = Math.Min(0.01, minimum * 0.05);
            double safeMargin = Math.Min(frameMargin + 0.01, minimum * 0.10);
            JObject Bounds(double margin) => new JObject
            {
                ["x_min_m"] = R9(margin),
                ["y_min_m"] = R9(margin),
                ["x_max_m"] = R9(width - margin),
                ["y_max_m"] = R9(height - margin)
            };
            return new JObject
            {
                ["sheet"] = new JObject
                {
                    ["name"] = sheet.GetName(),
                    ["format_name"] = Path.GetFileNameWithoutExtension(templatePath),
                    ["width_m"] = R9(width),
                    ["height_m"] = R9(height)
                },
                ["projection_method"] = values[4] != 0.0 ? "first_angle" : "third_angle",
                ["sheet_scale"] = new JObject
                {
                    ["numerator"] = R9(values[2]),
                    ["denominator"] = R9(values[3])
                },
                ["inner_frame"] = new JObject
                {
                    ["bounds_sheet_m"] = Bounds(frameMargin),
                    ["safe_zone_sheet_m"] = Bounds(safeMargin)
                },
                ["reserved_zones"] = new JArray()
            };
        }

        private JObject BuildInitializerGeometryReport(IModelDoc2 model, string modelPath,
            string configuration, string displayState)
        {
            var part = model as IPartDoc;
            if (part == null) throw new InvalidDataException("The source model is not a part.");
            var result = new JObject
            {
                ["schema_version"] = "1.0",
                ["status"] = "success",
                ["source"] = "q3ds-repository-initializer",
                ["model_path"] = modelPath,
                ["configuration"] = configuration,
                ["display_state"] = displayState
            };
            var box = part.GetPartBox(true) as double[];
            if (box == null || box.Length < 6)
                throw new InvalidDataException("GetPartBox did not return six finite coordinates.");
            result["part_box_m"] = new JObject
            {
                ["x_min_m"] = R9(box[0]), ["y_min_m"] = R9(box[1]),
                ["z_min_m"] = R9(box[2]), ["x_max_m"] = R9(box[3]),
                ["y_max_m"] = R9(box[4]), ["z_max_m"] = R9(box[5])
            };

            var bodyRows = new JArray();
            object[] bodies = part.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies != null)
            {
                for (int bodyIndex = 0; bodyIndex < bodies.Length; bodyIndex++)
                {
                    var body = bodies[bodyIndex] as IBody2;
                    if (body != null) bodyRows.Add(BuildInitializerBody(model, body, bodyIndex));
                }
            }
            result["bodies"] = bodyRows;
            result["body_count"] = bodyRows.Count;
            try
            {
                int status = 0;
                object raw = model.GetMassProperties2(ref status);
                double[] properties = raw as double[];
                if (properties != null && properties.Length >= 6)
                    result["mass_properties"] = new JObject
                    {
                        ["center_of_mass_m"] = new JArray(R9(properties[0]), R9(properties[1]), R9(properties[2])),
                        ["volume_m3"] = R12(properties[3]),
                        ["surface_area_m2"] = R12(properties[4]),
                        ["mass_kg"] = R12(properties[5])
                    };
            }
            catch { }
            return result;
        }

        private JObject BuildInitializerBody(IModelDoc2 model, IBody2 body, int bodyIndex)
        {
            string bodyId = "B" + bodyIndex.ToString(CultureInfo.InvariantCulture);
            object[] rawEdges = body.GetEdges() as object[] ?? new object[0];
            var edges = rawEdges.Select(item => item as IEdge).Where(item => item != null).ToList();
            var edgeRows = new JArray();
            for (int index = 0; index < edges.Count; index++)
                edgeRows.Add(BuildInitializerEdge(edges[index], bodyId + "E" + index));

            object[] rawFaces = body.GetFaces() as object[] ?? new object[0];
            var faceRows = new JArray();
            int faceIndex = 0;
            foreach (object item in rawFaces)
            {
                var face = item as IFace2;
                if (face == null) continue;
                faceRows.Add(BuildInitializerFace(face, bodyId + "F" + faceIndex,
                    edges, bodyId));
                faceIndex++;
            }
            return new JObject
            {
                ["id"] = bodyId,
                ["face_count"] = faceRows.Count,
                ["edge_count"] = edgeRows.Count,
                ["vertex_count"] = body.GetVertexCount(),
                ["faces"] = faceRows,
                ["edges"] = edgeRows
            };
        }

        private JObject BuildInitializerEdge(IEdge edge, string id)
        {
            var row = new JObject { ["id"] = id };
            try
            {
                var curve = edge.IGetCurve();
                if (curve != null && curve.IsCircle())
                {
                    var circle = curve.CircleParams as double[];
                    if (circle != null && circle.Length >= 7)
                    {
                        row["curve_type"] = "circle";
                        row["curve_parameters"] = new JArray(circle.Take(7).Select(R9));
                    }
                }
                else if (curve != null && curve.IsLine()) row["curve_type"] = "line";
                else row["curve_type"] = "other";
                var parameters = edge.GetCurveParams2() as double[];
                if (parameters != null && parameters.Length >= 6)
                {
                    row["start_model_m"] = new JArray(R9(parameters[0]), R9(parameters[1]), R9(parameters[2]));
                    row["end_model_m"] = new JArray(R9(parameters[3]), R9(parameters[4]), R9(parameters[5]));
                }
            }
            catch { if (row["curve_type"] == null) row["curve_type"] = "unknown"; }
            return row;
        }

        private JObject BuildInitializerFace(IFace2 face, string id, IList<IEdge> bodyEdges,
            string bodyId)
        {
            var row = new JObject { ["id"] = id };
            try { row["area_m2"] = R12(face.GetArea()); } catch { }
            try
            {
                var surface = face.IGetSurface();
                if (surface != null && surface.IsPlane())
                {
                    row["surface_type"] = "plane";
                    var normal = face.Normal as double[];
                    var point = FacePoint(face, surface);
                    row["surface_parameters"] = new JObject
                    {
                        ["origin"] = point != null ? new JArray(point.Select(R9)) : null,
                        ["axis"] = normal != null && normal.Length >= 3
                            ? new JArray(normal.Take(3).Select(R9)) : null
                    };
                }
                else if (surface != null && surface.IsCylinder())
                {
                    row["surface_type"] = "cylinder";
                    var cylinder = surface.CylinderParams as double[];
                    if (cylinder != null && cylinder.Length >= 7)
                        row["surface_parameters"] = new JObject
                        {
                            ["origin"] = new JArray(cylinder.Take(3).Select(R9)),
                            ["axis"] = new JArray(cylinder.Skip(3).Take(3).Select(R9)),
                            ["radius_m"] = R9(cylinder[6])
                        };
                }
                else row["surface_type"] = "other";
            }
            catch { if (row["surface_type"] == null) row["surface_type"] = "unknown"; }

            var faceEdgeIds = new JArray();
            object[] faceEdges = face.GetEdges() as object[];
            if (faceEdges != null)
                foreach (object item in faceEdges)
                {
                    var edge = item as IEdge;
                    int index = FindComObject(bodyEdges, edge);
                    if (index >= 0) faceEdgeIds.Add(bodyId + "E" + index);
                }
            row["edge_ids"] = faceEdgeIds;

            var loops = new JArray();
            object[] rawLoops = face.GetLoops() as object[];
            if (rawLoops != null)
            {
                int loopIndex = 0;
                foreach (object item in rawLoops)
                {
                    var loop = item as ILoop2;
                    if (loop == null) continue;
                    var loopEdges = new JArray();
                    object[] rawLoopEdges = loop.GetEdges() as object[];
                    if (rawLoopEdges != null)
                        foreach (object rawEdge in rawLoopEdges)
                        {
                            int index = FindComObject(bodyEdges, rawEdge as IEdge);
                            if (index >= 0) loopEdges.Add(bodyId + "E" + index);
                        }
                    loops.Add(new JObject
                    {
                        ["id"] = id + "L" + loopIndex,
                        ["outer"] = loop.IsOuter(),
                        ["edge_ids"] = loopEdges
                    });
                    loopIndex++;
                }
            }
            row["loops"] = loops;
            return row;
        }

        private static double[] FacePoint(IFace2 face, ISurface surface)
        {
            try
            {
                var bounds = face.GetUVBounds() as double[];
                if (bounds == null || bounds.Length < 4) return null;
                var point = surface.Evaluate((bounds[0] + bounds[1]) / 2.0,
                    (bounds[2] + bounds[3]) / 2.0, 0, 0) as double[];
                return point != null && point.Length >= 3 ? point.Take(3).ToArray() : null;
            }
            catch { return null; }
        }

        private static int FindComObject(IList<IEdge> candidates, IEdge target)
        {
            if (target == null) return -1;
            for (int index = 0; index < candidates.Count; index++)
                if (SameComObject(candidates[index], target)) return index;
            return -1;
        }

        private static bool SameComObject(object left, object right)
        {
            if (ReferenceEquals(left, right)) return true;
            if (left == null || right == null) return false;
            IntPtr leftPtr = IntPtr.Zero;
            IntPtr rightPtr = IntPtr.Zero;
            try
            {
                leftPtr = Marshal.GetIUnknownForObject(left);
                rightPtr = Marshal.GetIUnknownForObject(right);
                return leftPtr == rightPtr;
            }
            catch { return false; }
            finally
            {
                if (leftPtr != IntPtr.Zero) Marshal.Release(leftPtr);
                if (rightPtr != IntPtr.Zero) Marshal.Release(rightPtr);
            }
        }

        private JObject BuildInitializerReadinessReport(string modelPath, string modelHash,
            string templatePath, string configuration, string displayState, int imageWidth,
            int imageHeight, JObject drawingContext, IDictionary<string, string> finalPaths,
            JObject geometryReport)
        {
            return new JObject
            {
                ["schema_version"] = "1.0",
                ["status"] = "ready",
                ["producer"] = new JObject
                {
                    ["name"] = "q3ds-repository-initializer",
                    ["version"] = "1.0.0"
                },
                ["generated_at_utc"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                ["solidworks_revision"] = _solidWorks.RevisionNumber(),
                ["model"] = new JObject
                {
                    ["path"] = modelPath,
                    ["sha256"] = modelHash,
                    ["configuration"] = configuration,
                    ["display_state"] = displayState,
                    ["source_model_read_only"] = true
                },
                ["drawing_template_path"] = templatePath,
                ["blank_drawing_path"] = finalPaths["blank_drawing"],
                ["image_size_px"] = new JObject
                {
                    ["width"] = imageWidth,
                    ["height"] = imageHeight
                },
                ["checks"] = new JObject
                {
                    ["model_exists"] = true,
                    ["model_saved"] = true,
                    ["solid_body_count"] = ((JArray)geometryReport["bodies"]).Count,
                    ["standard_views_resolved"] = true,
                    ["standard_view_images_written"] = 6,
                    ["blank_drawing_saved"] = true,
                    ["blank_drawing_read_only_reopen_verified"] = true,
                    ["all_outputs_no_overwrite"] = true
                },
                ["drawing_context"] = drawingContext,
                ["blocking_issues"] = new JArray(),
                ["open_questions"] = new JArray()
            };
        }

        private static double R9(double value) { return Math.Round(value, 9); }
        private static double R12(double value) { return Math.Round(value, 12); }
    }
}
