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
using SolidworksExecution.Contracts;
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

            string semanticProfile = parameters != null
                ? parameters.Value<string>("semantic_feature_profile") ?? "none" : "none";
            bool emitSemanticFeatures = string.Equals(semanticProfile, "m1-experimental",
                StringComparison.Ordinal);
            if (!emitSemanticFeatures && !string.Equals(semanticProfile, "none",
                StringComparison.Ordinal))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_SEMANTIC_FEATURE_PROFILE",
                    "semantic_feature_profile must be none or m1-experimental.");

            var finalPaths = InitializerFinalPaths(publicationDirectory, emitSemanticFeatures);
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

                string stagedSemantic = null;
                string stagedTaxonomy = null;
                string taxonomyHash = null;
                if (emitSemanticFeatures)
                {
                    stagedSemantic = Path.Combine(stagingDirectory,
                        Path.GetFileName(finalPaths["semantic_features"]));
                    string taxonomySource = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                        "contracts", "mechanical-features-1.0.0-experimental.json");
                    if (!File.Exists(taxonomySource))
                        return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                            "SEMANTIC_TAXONOMY_UNAVAILABLE", "Experimental taxonomy artifact is not deployed.");
                    stagedTaxonomy = Path.Combine(stagingDirectory,
                        Path.GetFileName(finalPaths["semantic_taxonomy"]));
                    File.Copy(taxonomySource, stagedTaxonomy, false);
                    taxonomyHash = ComputeFileSha256(stagedTaxonomy);
                    JObject semanticFeatures = BuildInitializerSemanticFeatures(
                        modelScope.Document, modelPath,
                        sourceHashBefore, modelScope.Configuration, displayState,
                        finalPaths["geometry_report"], ComputeFileSha256(stagedGeometry),
                        finalPaths["semantic_taxonomy"], taxonomyHash, geometryReport);
                    File.WriteAllText(stagedSemantic,
                        semanticFeatures.ToString(Formatting.Indented) + System.Environment.NewLine,
                        new UTF8Encoding(false));
                }

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
                if (emitSemanticFeatures)
                {
                    artifactHashes["semantic_features"] = ComputeFileSha256(stagedSemantic);
                    artifactHashes["semantic_taxonomy"] = taxonomyHash;
                }
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
                if (emitSemanticFeatures)
                {
                    manifest["semantic_features"] = new JObject
                    {
                        ["path"] = finalPaths["semantic_features"],
                        ["sha256"] = artifactHashes["semantic_features"]
                    };
                    manifest["semantic_taxonomy"] = new JObject
                    {
                        ["path"] = finalPaths["semantic_taxonomy"],
                        ["sha256"] = artifactHashes["semantic_taxonomy"]
                    };
                }
                string stagedManifest = Path.Combine(stagingDirectory,
                    Path.GetFileName(finalPaths["manifest"]));
                File.WriteAllText(stagedManifest,
                    manifest.ToString(Formatting.Indented) + System.Environment.NewLine,
                    new UTF8Encoding(false));

                var commitKeys = new List<string>(new[] { "blank_drawing", "readiness_report",
                    "geometry_report", "image:front", "image:back", "image:left",
                    "image:right", "image:top", "image:bottom" });
                if (emitSemanticFeatures)
                    commitKeys.AddRange(new[] { "semantic_taxonomy", "semantic_features" });
                foreach (string key in commitKeys)
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
                if (emitSemanticFeatures)
                {
                    var semanticResult = response.ResultGeometry as JObject;
                    if (semanticResult != null)
                    {
                        semanticResult["semantic_features_path"] = finalPaths["semantic_features"];
                        semanticResult["semantic_features_sha256"] = artifactHashes["semantic_features"];
                        semanticResult["semantic_feature_profile"] = semanticProfile;
                    }
                }
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

        private static Dictionary<string, string> InitializerFinalPaths(string directory,
            bool includeSemanticFeatures)
        {
            var paths = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["manifest"] = Path.Combine(directory, "drawing-planning-handoff.json"),
                ["blank_drawing"] = Path.Combine(directory, "initializer-blank.SLDDRW"),
                ["readiness_report"] = Path.Combine(directory, "drawing-readiness.json"),
                ["geometry_report"] = Path.Combine(directory, "model-geometry.json")
            };
            if (includeSemanticFeatures)
            {
                paths["semantic_features"] = Path.Combine(directory,
                    "model-semantic-features.json");
                paths["semantic_taxonomy"] = Path.Combine(directory,
                    "mechanical-features-1.0.0-experimental.json");
            }
            foreach (string view in InitializerStandardViews)
                paths["image:" + view] = Path.Combine(directory, view + ".png");
            return paths;
        }

        // Promote only repository-controlled feature-tree types and typed FeatureData. Names and
        // rendered geometry never imply manufacturing semantics. Evidence that cannot be bound to
        // the frozen B-Rep remains an open question, so this experimental artifact stays honest.
        private JObject BuildInitializerSemanticFeatures(IModelDoc2 model, string modelPath,
            string modelHash,
            string configuration, string displayState, string geometryPath,
            string geometryHash, string taxonomyPath, string taxonomyHash,
            JObject geometry)
        {
            var features = new JArray();
            var bodies = geometry["bodies"] as JArray ?? new JArray();
            foreach (JObject body in bodies.OfType<JObject>())
            {
                string bodyId = body.Value<string>("id");
                if (string.IsNullOrWhiteSpace(bodyId)) continue;
                features.Add(new JObject
                {
                    ["feature_id"] = "FT-OVERALL-" + bodyId.Substring(1),
                    ["feature_class"] = "overall.prismatic_or_plate",
                    ["parent_feature_id"] = null,
                    ["source_feature_ref"] = null,
                    ["significance"] = new JArray(),
                    ["geometry_refs"] = new JObject
                    {
                        ["body_ids"] = new JArray(bodyId),
                        ["face_ids"] = new JArray(body["faces"] == null ? new JArray() :
                            body["faces"].Values<string>("id")),
                        ["edge_ids"] = new JArray(body["edges"] == null ? new JArray() :
                            body["edges"].Values<string>("id")),
                        ["vertex_ids"] = new JArray()
                    },
                    ["axis"] = null,
                    ["normal"] = null,
                    ["opening_count"] = null,
                    ["axial_extent"] = null,
                    ["occurrences"] = new JArray(),
                    ["evidence_status"] = "partial"
                });
            }

            var typedQuestions = new List<string>();
            var semanticFeaturesBySource = new List<Tuple<IFeature, string>>();
            int typedIndex = 0;
            foreach (IFeature feature in EnumerateInitializerFeatures(model))
            {
                string typeName = null;
                string sourceName = null;
                try
                {
                    typeName = feature.GetTypeName2() ?? string.Empty;
                    sourceName = feature.Name;
                }
                catch { continue; }

                bool? extrudeIsBoss = null;
                int? wizardHoleType = null;
                int? openingCount = null;
                bool through = false;
                int profileCircleCount = 0;
                bool profileHasOtherGeometry = false;
                JObject holeSpecification = null;
                try
                {
                    var wizard = feature.GetDefinition() as IWizardHoleFeatureData2;
                    if (wizard != null)
                    {
                        wizardHoleType = wizard.Type;
                        through = SemanticFeatureTypeContract.IsThroughWizardHole(wizardHoleType);
                        try { openingCount = wizard.GetSketchPointCount(); } catch { }
                        holeSpecification = ReadInitializerWizardHoleSpecification(wizard);
                    }
                    var simpleHole = feature.GetDefinition() as ISimpleHoleFeatureData;
                    if (simpleHole != null)
                    {
                        if (!openingCount.HasValue) openingCount = 1;
                        holeSpecification = ReadInitializerSimpleHoleSpecification(simpleHole);
                    }
                    var extrude = feature.GetDefinition() as IExtrudeFeatureData;
                    if (extrude != null)
                    {
                        extrudeIsBoss = extrude.IsBossFeature();
                        int endCondition = extrude.GetEndCondition(true);
                        through = endCondition == (int)swEndConditions_e.swEndCondThroughAll ||
                            endCondition == (int)swEndConditions_e.swEndCondThroughAllBoth ||
                            endCondition == (int)swEndConditions_e.swEndCondThroughNext;
                        ReadInitializerExtrudeProfile(feature, out profileCircleCount,
                            out profileHasOtherGeometry);
                        holeSpecification = ReadInitializerExtrudedCutSpecification(extrude);
                    }
                }
                catch { }

                string featureClass = SemanticFeatureTypeContract.Classify(typeName,
                    extrudeIsBoss, wizardHoleType);
                if (extrudeIsBoss == false && profileCircleCount > 0)
                {
                    featureClass = SemanticFeatureTypeContract.ClassifyExtrudedCutProfile(
                        profileCircleCount, profileHasOtherGeometry, through);
                    if (featureClass.StartsWith("geometry.hole", StringComparison.Ordinal))
                        openingCount = profileCircleCount;
                }
                if (featureClass == null) continue;

                JObject geometryRefs = BuildInitializerFeatureGeometryRefs(model, feature,
                    geometry);
                string geometryLineage = null;
                if (!InitializerGeometryHasBody(geometryRefs) && string.Equals(typeName, "ICE",
                    StringComparison.OrdinalIgnoreCase))
                    geometryRefs = ResolveInitializerHistoricalGeometryRefs(model, feature,
                        geometry, out geometryLineage);
                if (!InitializerGeometryHasBody(geometryRefs) && string.Equals(typeName,
                    "CosmeticThread", StringComparison.OrdinalIgnoreCase))
                    geometryRefs = ResolveInitializerCosmeticThreadGeometryRefs(model, feature,
                        geometry);
                var bodyIds = geometryRefs["body_ids"] as JArray;
                if (bodyIds == null || bodyIds.Count == 0)
                {
                    typedQuestions.Add(sourceName + " (" + typeName +
                        ") has typed FeatureData but no frozen B-Rep binding.");
                    continue;
                }

                typedIndex++;
                string semanticId = "FT-TYPED-" + typedIndex.ToString("D4",
                    CultureInfo.InvariantCulture);
                string parentId = "FT-OVERALL-" + bodyIds[0].Value<string>().Substring(1);
                JObject axis = AxisFromFrozenGeometry(geometryRefs, geometry);
                JObject axialExtent = string.Equals(featureClass, "geometry.hole.through",
                    StringComparison.Ordinal) ? BuildInitializerThroughHoleExtent(
                    geometryRefs, axis, geometry) : null;
                var row = new JObject
                {
                    ["feature_id"] = semanticId,
                    ["feature_class"] = featureClass,
                    ["parent_feature_id"] = parentId,
                    ["source_feature_ref"] = sourceName,
                    ["significance"] = new JArray(),
                    ["geometry_refs"] = geometryRefs,
                    ["axis"] = axis,
                    ["normal"] = null,
                    ["opening_count"] = openingCount.HasValue ?
                        new JValue(Math.Max(0, openingCount.Value)) : JValue.CreateNull(),
                    ["axial_extent"] = axialExtent,
                    ["hole_specification"] = featureClass.StartsWith("geometry.hole",
                        StringComparison.Ordinal) ? holeSpecification : null,
                    ["occurrences"] = new JArray(),
                    ["evidence_status"] = "partial"
                };
                features.Add(row);
                semanticFeaturesBySource.Add(Tuple.Create(feature, semanticId));
                if (featureClass.StartsWith("geometry.hole", StringComparison.Ordinal) &&
                    (axis == null || !openingCount.HasValue))
                    typedQuestions.Add(sourceName +
                        " lacks a complete typed hole axis/opening binding.");
                if (string.Equals(featureClass, "geometry.hole.through",
                    StringComparison.Ordinal) && axialExtent == null)
                    typedQuestions.Add(sourceName +
                        " is typed as through but its two frozen axial end circles are unresolved.");
                if (through && featureClass.StartsWith("geometry.hole",
                    StringComparison.Ordinal) && axis == null)
                    typedQuestions.Add(sourceName +
                        " is typed as through but its cylinder axis is not frozen.");
            }

            var relations = BuildInitializerSemanticRelations(model, semanticFeaturesBySource,
                features, geometry, typedQuestions);
            var openQuestions = new JArray
            {
                new JObject
                {
                    ["question_id"] = "Q-SEMANTIC-FEATURE-DATA",
                    ["code"] = "CONTROLLED_SEMANTICS_REQUIRED",
                    ["feature_ids"] = new JArray(features.Values<string>("feature_id")),
                    ["impact"] = "The source model does not prove drawing-required scope, functional/manufacturing/inspection significance, or external acceptance requirements. Downstream closed-set feature coverage must remain blocked, while geometry-only reasoning may continue with explicit uncertainty.",
                    ["required_source"] = "Optional hash-bound model PMI or controlled engineering requirement input.",
                    ["resolution_kind"] = "optional_controlled_input"
                }
            };
            if (typedQuestions.Count > 0)
                openQuestions.Add(new JObject
                {
                    ["question_id"] = "Q-SEMANTIC-FEATURE-GEOMETRY",
                    ["code"] = "FEATURE_GEOMETRY_INCOMPLETE",
                    ["feature_ids"] = new JArray(),
                    ["impact"] = string.Join(" ", typedQuestions.Distinct().Take(12)),
                    ["required_source"] = "Additional exact SolidWorks FeatureData, occurrence, suppression, start/end-face, or persistent B-Rep readback from the source model.",
                    ["resolution_kind"] = "model_extraction_limit"
                });
            return new JObject
            {
                ["protocol_id"] = "q3ds-solidworks-model-semantic-features",
                ["schema_version"] = "1.0",
                ["artifact_id"] = "MSF-" + modelHash.Substring(0, 16),
                ["status"] = "incomplete",
                ["model_evidence_status"] = "exhausted",
                ["controlled_semantics_status"] = "unresolved",
                ["producer"] = new JObject
                {
                    ["name"] = "q3ds-repository-initializer",
                    ["version"] = "1.2.0",
                    ["extraction_mode"] = "csharp_initializer"
                },
                ["model"] = new JObject
                {
                    ["path"] = modelPath,
                    ["sha256"] = modelHash,
                    ["configuration"] = configuration,
                    ["display_state"] = displayState
                },
                ["geometry_report"] = new JObject
                {
                    ["path"] = geometryPath,
                    ["sha256"] = geometryHash
                },
                ["taxonomy"] = new JObject
                {
                    ["taxonomy_id"] = "mechanical-features",
                    ["taxonomy_version"] = "1.0.0-experimental",
                    ["path"] = taxonomyPath,
                    ["sha256"] = taxonomyHash
                },
                ["features"] = features,
                ["relations"] = relations,
                ["required_feature_ids"] = new JArray(),
                ["exemptions"] = new JArray(),
                ["open_questions"] = openQuestions
            };
        }

        private static JObject BuildInitializerThroughHoleExtent(JObject geometryRefs,
            JObject axis, JObject geometry)
        {
            if (geometryRefs == null || axis == null) return null;
            var direction = axis["direction"] as JArray;
            if (direction == null || direction.Count != 3) return null;
            double dx = direction[0].Value<double>();
            double dy = direction[1].Value<double>();
            double dz = direction[2].Value<double>();
            var projections = new List<double>();
            foreach (string edgeId in geometryRefs["edge_ids"] == null
                ? Enumerable.Empty<string>() : geometryRefs["edge_ids"].Values<string>())
            {
                JObject edge = FindInitializerGeometryEdge(geometry, edgeId);
                var parameters = edge == null ? null : edge["curve_parameters"] as JArray;
                if (edge == null || edge.Value<string>("curve_type") != "circle" ||
                    parameters == null || parameters.Count < 7) continue;
                double ex = parameters[0].Value<double>();
                double ey = parameters[1].Value<double>();
                double ez = parameters[2].Value<double>();
                double enx = parameters[3].Value<double>();
                double eny = parameters[4].Value<double>();
                double enz = parameters[5].Value<double>();
                double dot = enx * dx + eny * dy + enz * dz;
                if (Math.Abs(Math.Abs(dot) - 1.0) > 1e-6) continue;
                double projection = ex * dx + ey * dy + ez * dz;
                if (!projections.Any(value => Math.Abs(value - projection) <= 1e-9))
                    projections.Add(projection);
            }
            if (projections.Count != 2) return null;
            double start = projections.Min();
            double end = projections.Max();
            double depth = end - start;
            if (depth <= 1e-12) return null;
            return new JObject
            {
                ["start_m"] = R9(start), ["end_m"] = R9(end),
                ["effective_depth_m"] = R9(depth), ["total_depth_m"] = R9(depth),
                ["bottom_form"] = "through"
            };
        }

        private static JObject ReadInitializerWizardHoleSpecification(
            IWizardHoleFeatureData2 wizard)
        {
            var row = new JObject { ["source_kind"] = "hole_wizard" };
            try { row["feature_type_code"] = wizard.Type; } catch { }
            try { row["end_condition_code"] = wizard.EndCondition; } catch { }
            TryAddInitializerPositive(row, "diameter_m", () => wizard.HoleDiameter,
                () => wizard.ThruHoleDiameter, () => wizard.Diameter);
            TryAddInitializerNonnegative(row, "hole_depth_m", () => wizard.HoleDepth,
                () => wizard.ThruHoleDepth, () => wizard.Depth);
            TryAddInitializerNonnegative(row, "thread_depth_m", () => wizard.ThreadDepth,
                () => wizard.TapDrillDepth, () => wizard.ThruTapDrillDepth);
            TryAddInitializerPositive(row, "thread_diameter_m", () => wizard.ThreadDiameter,
                () => wizard.MajorDiameter, () => wizard.MinorDiameter);
            TryAddInitializerNonnegative(row, "counterbore_depth_m",
                () => wizard.CounterBoreDepth);
            TryAddInitializerPositive(row, "counterbore_diameter_m",
                () => wizard.CounterBoreDiameter);
            TryAddInitializerPositive(row, "countersink_diameter_m",
                () => wizard.CounterSinkDiameter);
            TryAddInitializerPositive(row, "countersink_angle_rad",
                () => wizard.CounterSinkAngle);
            TryAddInitializerPositive(row, "drill_angle_rad", () => wizard.DrillAngle);
            return row;
        }

        private static JObject ReadInitializerSimpleHoleSpecification(
            ISimpleHoleFeatureData hole)
        {
            var row = new JObject { ["source_kind"] = "simple_hole" };
            try { row["feature_type_code"] = hole.Type; } catch { }
            TryAddInitializerPositive(row, "diameter_m", () => hole.Diameter);
            TryAddInitializerNonnegative(row, "hole_depth_m", () => hole.Depth);
            return row;
        }

        private static JObject ReadInitializerExtrudedCutSpecification(
            IExtrudeFeatureData extrude)
        {
            if (extrude == null || extrude.IsBossFeature()) return null;
            var row = new JObject { ["source_kind"] = "extruded_cut" };
            try { row["end_condition_code"] = extrude.GetEndCondition(true); } catch { }
            TryAddInitializerNonnegative(row, "hole_depth_m", () => extrude.GetDepth(true));
            return row;
        }

        private static void TryAddInitializerPositive(JObject row, string name,
            params Func<double>[] readers)
        {
            foreach (Func<double> reader in readers)
                try
                {
                    double value = reader();
                    if (value > 0 && !double.IsNaN(value) && !double.IsInfinity(value))
                    {
                        row[name] = R9(value);
                        return;
                    }
                }
                catch { }
        }

        private static void TryAddInitializerNonnegative(JObject row, string name,
            params Func<double>[] readers)
        {
            bool observedZero = false;
            foreach (Func<double> reader in readers)
                try
                {
                    double value = reader();
                    if (value > 0 && !double.IsNaN(value) && !double.IsInfinity(value))
                    {
                        row[name] = R9(value);
                        return;
                    }
                    if (value == 0) observedZero = true;
                }
                catch { }
            if (observedZero) row[name] = 0.0;
        }

        private static void ReadInitializerExtrudeProfile(IFeature feature,
            out int fullCircleCount, out bool hasOtherGeometry)
        {
            fullCircleCount = 0;
            hasOtherGeometry = false;
            object[] parents = feature.GetParents() as object[];
            if (parents == null) return;
            foreach (IFeature parent in parents.OfType<IFeature>())
            {
                ISketch sketch = parent.GetSpecificFeature2() as ISketch;
                if (sketch == null) continue;
                object[] segments = sketch.GetSketchSegments() as object[];
                if (segments == null) continue;
                foreach (ISketchSegment segment in segments.OfType<ISketchSegment>())
                {
                    bool construction = false;
                    try { construction = segment.ConstructionGeometry; } catch { }
                    if (construction) continue;
                    var arc = segment as ISketchArc;
                    bool fullCircle = false;
                    try { fullCircle = arc != null && arc.IsCircle() != 0; } catch { }
                    if (fullCircle) fullCircleCount++;
                    else hasOtherGeometry = true;
                }
                return;
            }
        }

        private static IEnumerable<IFeature> EnumerateInitializerFeatures(IModelDoc2 model)
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var feature = model.FirstFeature() as IFeature;
            int guard = 0;
            while (feature != null && guard++ < 5000)
            {
                string key = null;
                try { key = (feature.GetTypeName2() ?? string.Empty) + "\n" + feature.Name; }
                catch { }
                if (key != null && seen.Add(key)) yield return feature;
                var sub = feature.GetFirstSubFeature() as IFeature;
                int subGuard = 0;
                while (sub != null && subGuard++ < 2000)
                {
                    string subKey = null;
                    try { subKey = (sub.GetTypeName2() ?? string.Empty) + "\n" + sub.Name; }
                    catch { }
                    if (subKey != null && seen.Add(subKey)) yield return sub;
                    sub = sub.GetNextSubFeature() as IFeature;
                }
                feature = feature.GetNextFeature() as IFeature;
            }
        }

        private JObject BuildInitializerFeatureGeometryRefs(IModelDoc2 model, IFeature feature,
            JObject geometry)
        {
            var bodyIds = new HashSet<string>(StringComparer.Ordinal);
            var faceIds = new HashSet<string>(StringComparer.Ordinal);
            var edgeIds = new HashSet<string>(StringComparer.Ordinal);
            var targetFaces = new List<IFace2>();
            try
            {
                object[] raw = feature.GetFaces() as object[];
                if (raw != null) targetFaces.AddRange(raw.OfType<IFace2>());
            }
            catch { }
            try
            {
                object[] raw = feature.GetAffectedFaces() as object[];
                if (raw != null) targetFaces.AddRange(raw.OfType<IFace2>());
            }
            catch { }

            var part = model as IPartDoc;
            object[] bodies = part != null
                ? part.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[] : null;
            var geometryBodies = geometry["bodies"] as JArray ?? new JArray();
            if (bodies != null)
            {
                for (int bodyIndex = 0; bodyIndex < bodies.Length; bodyIndex++)
                {
                    var body = bodies[bodyIndex] as IBody2;
                    if (body == null) continue;
                    string bodyId = "B" + bodyIndex.ToString(CultureInfo.InvariantCulture);
                    JObject geometryBody = geometryBodies.OfType<JObject>().FirstOrDefault(
                        row => row.Value<string>("id") == bodyId);
                    object[] bodyFaces = body.GetFaces() as object[] ?? new object[0];
                    for (int faceIndex = 0; faceIndex < bodyFaces.Length; faceIndex++)
                    {
                        var candidate = bodyFaces[faceIndex] as IFace2;
                        if (candidate == null || !targetFaces.Any(target =>
                            SameComObject(candidate, target))) continue;
                        string faceId = bodyId + "F" + faceIndex.ToString(
                            CultureInfo.InvariantCulture);
                        bodyIds.Add(bodyId);
                        faceIds.Add(faceId);
                        JObject geometryFace = geometryBody == null ? null :
                            (geometryBody["faces"] as JArray ?? new JArray()).OfType<JObject>()
                            .FirstOrDefault(row => row.Value<string>("id") == faceId);
                        if (geometryFace != null)
                            foreach (string edgeId in geometryFace["edge_ids"] == null
                                ? Enumerable.Empty<string>()
                                : geometryFace["edge_ids"].Values<string>())
                                edgeIds.Add(edgeId);
                    }
                }
            }

            // Some FeatureData exposes a body but no face array (notably relation/container
            // features). Bind only the body identity and retain partial evidence.
            if (bodyIds.Count == 0)
            {
                try
                {
                    var featureBody = feature.GetBody() as IBody2;
                    if (featureBody != null && bodies != null)
                        for (int index = 0; index < bodies.Length; index++)
                            if (SameComObject(featureBody, bodies[index]))
                                bodyIds.Add("B" + index.ToString(CultureInfo.InvariantCulture));
                }
                catch { }
            }
            return new JObject
            {
                ["body_ids"] = new JArray(bodyIds.OrderBy(value => value)),
                ["face_ids"] = new JArray(faceIds.OrderBy(value => value)),
                ["edge_ids"] = new JArray(edgeIds.OrderBy(value => value)),
                ["vertex_ids"] = new JArray()
            };
        }

        private static bool InitializerGeometryHasBody(JObject geometryRefs)
        {
            var bodyIds = geometryRefs == null ? null : geometryRefs["body_ids"] as JArray;
            return bodyIds != null && bodyIds.Count > 0;
        }

        private JObject ResolveInitializerCosmeticThreadGeometryRefs(IModelDoc2 model,
            IFeature feature, JObject geometry)
        {
            bool accessed = false;
            var thread = feature.GetDefinition() as ICosmeticThreadFeatureData;
            if (thread == null) return EmptyInitializerGeometryRefs();
            try
            {
                accessed = thread.AccessSelections(model, null);
                var edge = thread.Edge as IEdge;
                var adjacent = edge == null ? null : edge.GetTwoAdjacentFaces2() as object[];
                var targetFaces = adjacent == null ? new List<IFace2>() :
                    adjacent.OfType<IFace2>().Where(candidate =>
                    {
                        try
                        {
                            var surface = candidate.IGetSurface();
                            return surface != null && surface.IsCylinder();
                        }
                        catch { return false; }
                    }).ToList();
                if (targetFaces.Count != 1) return EmptyInitializerGeometryRefs();
                return BuildInitializerFaceGeometryRefs(model, targetFaces[0], geometry);
            }
            catch { return EmptyInitializerGeometryRefs(); }
            finally
            {
                if (accessed) try { thread.ReleaseSelectionAccess(); } catch { }
            }
        }

        private JObject BuildInitializerFaceGeometryRefs(IModelDoc2 model, IFace2 target,
            JObject geometry)
        {
            var part = model as IPartDoc;
            object[] bodies = part == null ? null : part.GetBodies2(
                (int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies != null)
                for (int bodyIndex = 0; bodyIndex < bodies.Length; bodyIndex++)
                {
                    var body = bodies[bodyIndex] as IBody2;
                    object[] faces = body == null ? null : body.GetFaces() as object[];
                    if (faces == null) continue;
                    for (int faceIndex = 0; faceIndex < faces.Length; faceIndex++)
                        if (SameComObject(faces[faceIndex], target))
                        {
                            string bodyId = "B" + bodyIndex.ToString(CultureInfo.InvariantCulture);
                            string faceId = bodyId + "F" + faceIndex.ToString(
                                CultureInfo.InvariantCulture);
                            JObject frozen = FindInitializerGeometryFace(geometry, faceId);
                            return new JObject
                            {
                                ["body_ids"] = new JArray(bodyId),
                                ["face_ids"] = new JArray(faceId),
                                ["edge_ids"] = new JArray(frozen == null || frozen["edge_ids"] == null
                                    ? Enumerable.Empty<string>() : frozen["edge_ids"].Values<string>()),
                                ["vertex_ids"] = new JArray()
                            };
                        }
                }
            return EmptyInitializerGeometryRefs();
        }

        private static JObject EmptyInitializerGeometryRefs()
        {
            return new JObject
            {
                ["body_ids"] = new JArray(), ["face_ids"] = new JArray(),
                ["edge_ids"] = new JArray(), ["vertex_ids"] = new JArray()
            };
        }

        // Imported/history (ICE) rows can survive in the feature tree after their own transient
        // faces disappear.  Accept current geometry only through an explicit SolidWorks ownership
        // or child relationship, and only when that lineage resolves to one unambiguous B-Rep.
        private JObject ResolveInitializerHistoricalGeometryRefs(IModelDoc2 model,
            IFeature feature, JObject geometry, out string lineage)
        {
            lineage = null;
            var candidates = new List<Tuple<IFeature, string>>();
            try
            {
                var owner = feature.GetOwnerFeature() as IFeature;
                if (owner != null && !SameComObject(owner, feature))
                    candidates.Add(Tuple.Create(owner, "owner"));
            }
            catch { }
            try
            {
                object[] children = feature.GetChildren() as object[];
                if (children != null)
                    foreach (IFeature child in children.OfType<IFeature>())
                        if (!SameComObject(child, feature))
                            candidates.Add(Tuple.Create(child, "child"));
            }
            catch { }

            JObject resolved = null;
            string resolvedLineage = null;
            foreach (Tuple<IFeature, string> candidate in candidates)
            {
                JObject refs = BuildInitializerFeatureGeometryRefs(model, candidate.Item1,
                    geometry);
                if (!InitializerGeometryHasBody(refs)) continue;
                if (resolved != null && !JToken.DeepEquals(resolved, refs))
                    return EmptyInitializerGeometryRefs();
                resolved = refs;
                resolvedLineage = candidate.Item2;
            }
            if (resolved == null)
                return EmptyInitializerGeometryRefs();
            lineage = resolvedLineage;
            return resolved;
        }

        private static JObject AxisFromFrozenGeometry(JObject geometryRefs, JObject geometry)
        {
            var wanted = new HashSet<string>(geometryRefs["face_ids"] == null
                ? Enumerable.Empty<string>() : geometryRefs["face_ids"].Values<string>(),
                StringComparer.Ordinal);
            foreach (JObject body in (geometry["bodies"] as JArray ?? new JArray())
                .OfType<JObject>())
                foreach (JObject face in (body["faces"] as JArray ?? new JArray())
                    .OfType<JObject>())
                {
                    if (!wanted.Contains(face.Value<string>("id")) ||
                        face.Value<string>("surface_type") != "cylinder") continue;
                    var parameters = face["surface_parameters"] as JObject;
                    var origin = parameters == null ? null : parameters["origin"] as JArray;
                    var direction = parameters == null ? null : parameters["axis"] as JArray;
                    if (origin != null && origin.Count == 3 && direction != null &&
                        direction.Count == 3)
                        return new JObject
                        {
                            ["origin_m"] = origin.DeepClone(),
                            ["direction"] = direction.DeepClone()
                        };
                }
            return null;
        }

        private JArray BuildInitializerSemanticRelations(IModelDoc2 model,
            IList<Tuple<IFeature, string>> semanticFeaturesBySource, JArray features,
            JObject geometry,
            IList<string> questions)
        {
            var relations = new JArray();
            int relationIndex = 0;
            foreach (IFeature feature in EnumerateInitializerFeatures(model))
            {
                string typeName;
                try { typeName = feature.GetTypeName2() ?? string.Empty; }
                catch { continue; }
                bool pattern = SemanticFeatureTypeContract.IsPattern(typeName);
                bool mirror = SemanticFeatureTypeContract.IsMirror(typeName);
                if (!pattern && !mirror) continue;
                var members = new HashSet<string>(StringComparer.Ordinal);
                bool accessed = false;
                object definition = null;
                JObject relationAxis = null;
                JArray mirrorPlaneNormal = null;
                int expectedOccurrences = 0;
                var skippedOccurrences = new HashSet<int>();
                var instanceTransforms = new List<double[]>();
                try
                {
                    definition = feature.GetDefinition();
                    object[] seeds = null;
                    var circular = definition as ICircularPatternFeatureData;
                    var linear = definition as ILinearPatternFeatureData;
                    var mirrorData = definition as IMirrorPatternFeatureData;
                    if (circular != null)
                    {
                        accessed = circular.AccessSelections(model, null);
                        seeds = circular.PatternFeatureArray as object[];
                        relationAxis = ReadInitializerRelationAxis(circular.Axis);
                        if (relationAxis == null)
                            relationAxis = ReadInitializerCircularPatternTransformAxis(circular);
                        expectedOccurrences = circular.TotalInstances;
                        ReadInitializerPatternTransforms(circular.GetTransform,
                            expectedOccurrences, instanceTransforms);
                        ReadInitializerSkippedItems(circular.SkippedItemArray,
                            skippedOccurrences);
                    }
                    else if (linear != null)
                    {
                        accessed = linear.AccessSelections(model, null);
                        seeds = linear.PatternFeatureArray as object[];
                        relationAxis = ReadInitializerRelationAxis(linear.D1Axis);
                        if (relationAxis == null)
                            relationAxis = ReadInitializerLinearPatternTransformAxis(linear);
                        expectedOccurrences = Math.Max(1, linear.D1TotalInstances);
                        try
                        {
                            if (linear.IsDirection2Specified())
                                expectedOccurrences *= Math.Max(1, linear.D2TotalInstances);
                        }
                        catch { }
                        ReadInitializerPatternTransforms(linear.GetTransform,
                            expectedOccurrences, instanceTransforms);
                        ReadInitializerSkippedItems(linear.SkippedItemArray,
                            skippedOccurrences);
                    }
                    else if (mirrorData != null)
                    {
                        accessed = mirrorData.AccessSelections(model, null);
                        seeds = mirrorData.PatternFeatureArray as object[];
                        mirrorPlaneNormal = ReadInitializerPlaneNormal(mirrorData.Plane);
                    }
                    if (seeds != null)
                        foreach (IFeature seed in seeds.OfType<IFeature>())
                        {
                            string semanticId = ResolveInitializerSemanticFeatureId(seed,
                                semanticFeaturesBySource);
                            if (!string.IsNullOrWhiteSpace(semanticId))
                                members.Add(semanticId);
                        }
                }
                catch { }
                finally
                {
                    if (accessed)
                    {
                        try
                        {
                            var circular = definition as ICircularPatternFeatureData;
                            var linear = definition as ILinearPatternFeatureData;
                            var mirrorData = definition as IMirrorPatternFeatureData;
                            if (circular != null) circular.ReleaseSelectionAccess();
                            else if (linear != null) linear.ReleaseSelectionAccess();
                            else if (mirrorData != null) mirrorData.ReleaseSelectionAccess();
                        }
                        catch { }
                    }
                }
                if (members.Count == 0)
                {
                    questions.Add(feature.Name + " (" + typeName +
                        ") has no seed that resolves to a typed semantic feature.");
                    continue;
                }
                if (pattern && relationAxis == null)
                {
                    questions.Add(feature.Name + " (" + typeName +
                        ") has a resolved seed but no readable frozen pattern axis.");
                    continue;
                }
                if (mirror && mirrorPlaneNormal == null)
                {
                    questions.Add(feature.Name + " (" + typeName +
                        ") has resolved members but no readable frozen mirror-plane normal.");
                    continue;
                }
                bool occurrencesFrozen = false;
                if (pattern && members.Count == 1 && expectedOccurrences > 0)
                {
                    string memberId = members.First();
                    JObject member = features.OfType<JObject>().FirstOrDefault(row =>
                        row.Value<string>("feature_id") == memberId);
                    JObject patternRefs = BuildInitializerFeatureGeometryRefs(model, feature,
                        geometry);
                    string occurrenceError;
                    occurrencesFrozen = TryFreezeInitializerPatternOccurrences(member,
                        patternRefs, geometry, expectedOccurrences, skippedOccurrences,
                        instanceTransforms,
                        out occurrenceError);
                    if (!occurrencesFrozen && !string.IsNullOrWhiteSpace(occurrenceError))
                        questions.Add(feature.Name + " (" + typeName + ") " + occurrenceError);
                }
                else if (mirror && members.Count == 1)
                {
                    string memberId = members.First();
                    JObject member = features.OfType<JObject>().FirstOrDefault(row =>
                        row.Value<string>("feature_id") == memberId);
                    JObject mirrorRefs = BuildInitializerFeatureGeometryRefs(model, feature,
                        geometry);
                    string occurrenceError;
                    occurrencesFrozen = TryFreezeInitializerMirrorOccurrences(member,
                        mirrorRefs, geometry, out occurrenceError);
                    if (!occurrencesFrozen && !string.IsNullOrWhiteSpace(occurrenceError))
                        questions.Add(feature.Name + " (" + typeName + ") " + occurrenceError);
                }
                relationIndex++;
                relations.Add(new JObject
                {
                    ["relation_id"] = "REL-TYPED-" + relationIndex.ToString("D4",
                        CultureInfo.InvariantCulture),
                    ["relation_class"] = pattern ? "relation.pattern" :
                        "relation.symmetry_or_mirror",
                    ["member_feature_ids"] = new JArray(members.OrderBy(value => value)),
                    ["axis"] = relationAxis,
                    ["plane_normal"] = mirrorPlaneNormal,
                    ["evidence_status"] = "partial"
                });
                if (!occurrencesFrozen)
                    questions.Add(feature.Name + " (" + typeName +
                        ") requires exact actual/suppressed occurrence geometry readback.");
            }
            return relations;
        }

        private static bool TryFreezeInitializerMirrorOccurrences(JObject member,
            JObject mirrorRefs, JObject geometry, out string error)
        {
            error = null;
            if (member == null)
            {
                error = "has no semantic member row for mirror occurrence binding.";
                return false;
            }
            var seedRefs = member["geometry_refs"] as JObject;
            var seedFaces = new HashSet<string>(seedRefs == null || seedRefs["face_ids"] == null
                ? Enumerable.Empty<string>() : seedRefs["face_ids"].Values<string>(),
                StringComparer.Ordinal);
            var mirroredFaces = new HashSet<string>(mirrorRefs == null ||
                mirrorRefs["face_ids"] == null ? Enumerable.Empty<string>() :
                mirrorRefs["face_ids"].Values<string>(), StringComparer.Ordinal);
            mirroredFaces.ExceptWith(seedFaces);
            if (seedFaces.Count == 0 || mirroredFaces.Count == 0)
            {
                error = "does not expose distinct non-empty seed and mirrored face sets.";
                return false;
            }
            if (seedFaces.Count != mirroredFaces.Count ||
                !InitializerSurfaceSignature(seedFaces, geometry).SequenceEqual(
                    InitializerSurfaceSignature(mirroredFaces, geometry),
                    StringComparer.Ordinal))
            {
                error = "does not expose complete topologically matching seed and mirrored face sets.";
                return false;
            }
            JObject frozenSeed = BuildInitializerGeometryRefsFromFaces(seedFaces, geometry);
            JObject frozenMirror = BuildInitializerGeometryRefsFromFaces(mirroredFaces, geometry);
            if (!InitializerGeometryHasBody(frozenSeed) ||
                !InitializerGeometryHasBody(frozenMirror))
            {
                error = "cannot bind both mirror occurrences to frozen bodies.";
                return false;
            }
            string memberId = member.Value<string>("feature_id");
            member["occurrences"] = new JArray(
                new JObject
                {
                    ["occurrence_id"] = "OCC-" + memberId + "-0001",
                    ["suppressed"] = false, ["geometry_refs"] = frozenSeed
                },
                new JObject
                {
                    ["occurrence_id"] = "OCC-" + memberId + "-0002",
                    ["suppressed"] = false, ["geometry_refs"] = frozenMirror
                });
            return true;
        }

        private static IEnumerable<string> InitializerSurfaceSignature(
            IEnumerable<string> faceIds, JObject geometry)
        {
            return faceIds.Select(faceId => FindInitializerGeometryFace(geometry, faceId))
                .Where(face => face != null)
                .Select(face => face.Value<string>("surface_type") ?? "unknown")
                .OrderBy(value => value, StringComparer.Ordinal);
        }

        private static JObject BuildInitializerGeometryRefsFromFaces(
            IEnumerable<string> faceIds, JObject geometry)
        {
            var bodies = new HashSet<string>(StringComparer.Ordinal);
            var faces = new HashSet<string>(StringComparer.Ordinal);
            var edges = new HashSet<string>(StringComparer.Ordinal);
            foreach (string faceId in faceIds)
            {
                JObject face = FindInitializerGeometryFace(geometry, faceId);
                int marker = string.IsNullOrWhiteSpace(faceId) ? -1 : faceId.IndexOf('F');
                if (face == null || marker <= 0) continue;
                faces.Add(faceId);
                bodies.Add(faceId.Substring(0, marker));
                if (face["edge_ids"] != null) edges.UnionWith(face["edge_ids"].Values<string>());
            }
            return new JObject
            {
                ["body_ids"] = new JArray(bodies.OrderBy(value => value)),
                ["face_ids"] = new JArray(faces.OrderBy(value => value)),
                ["edge_ids"] = new JArray(edges.OrderBy(value => value)),
                ["vertex_ids"] = new JArray()
            };
        }

        private static void ReadInitializerSkippedItems(object raw,
            ISet<int> skipped)
        {
            var values = raw as Array;
            if (values == null) return;
            foreach (object value in values)
                try { skipped.Add(Convert.ToInt32(value, CultureInfo.InvariantCulture)); }
                catch { }
        }

        private static void ReadInitializerPatternTransforms(
            Func<int, MathTransform> readTransform, int count, IList<double[]> frozen)
        {
            for (int index = 0; index < count; index++)
            {
                try
                {
                    var transform = readTransform(index);
                    double[] values = transform == null ? null : transform.ArrayData as double[];
                    if (values == null || values.Length < 12)
                    {
                        frozen.Clear();
                        return;
                    }
                    frozen.Add((double[])values.Clone());
                }
                catch
                {
                    frozen.Clear();
                    return;
                }
            }
        }

        private static string ResolveInitializerSemanticFeatureId(IFeature seed,
            IList<Tuple<IFeature, string>> semanticFeaturesBySource)
        {
            if (seed == null) return null;
            var matches = new HashSet<string>(StringComparer.Ordinal);
            foreach (Tuple<IFeature, string> row in semanticFeaturesBySource)
                if (SameComObject(seed, row.Item1)) matches.Add(row.Item2);
            if (matches.Count == 1) return matches.First();
            if (matches.Count > 1) return null;

            var frontier = new List<IFeature> { seed };
            var visited = new List<IFeature> { seed };
            for (int depth = 0; depth < 4 && frontier.Count > 0; depth++)
            {
                var next = new List<IFeature>();
                foreach (IFeature feature in frontier)
                {
                    var immediate = new List<IFeature>();
                    AddInitializerRelatedFeatures(feature, immediate);
                    foreach (IFeature candidate in immediate)
                    {
                        if (visited.Any(row => SameComObject(row, candidate))) continue;
                        visited.Add(candidate);
                        next.Add(candidate);
                    }
                }
                foreach (Tuple<IFeature, string> row in semanticFeaturesBySource)
                    if (next.Any(candidate => SameComObject(candidate, row.Item1)))
                        matches.Add(row.Item2);
                if (matches.Count > 0) return matches.Count == 1 ? matches.First() : null;
                frontier = next;
            }
            return null;
        }

        private static void AddInitializerRelatedFeatures(IFeature feature,
            IList<IFeature> related)
        {
            try
            {
                var owner = feature.GetOwnerFeature() as IFeature;
                if (owner != null && !SameComObject(owner, feature)) related.Add(owner);
            }
            catch { }
            try
            {
                object[] parents = feature.GetParents() as object[];
                if (parents != null)
                    foreach (IFeature parent in parents.OfType<IFeature>())
                        related.Add(parent);
            }
            catch { }
            try
            {
                object[] children = feature.GetChildren() as object[];
                if (children != null)
                    foreach (IFeature child in children.OfType<IFeature>())
                        related.Add(child);
            }
            catch { }
        }

        private static bool TryFreezeInitializerPatternOccurrences(JObject member,
            JObject patternRefs, JObject geometry, int expectedOccurrences,
            ISet<int> skippedIndices, IList<double[]> instanceTransforms, out string error)
        {
            error = null;
            if (member == null)
            {
                error = "has no semantic member row for occurrence binding.";
                return false;
            }
            var memberRefs = member["geometry_refs"] as JObject;
            var seedFaceIds = memberRefs == null ? new JArray() :
                memberRefs["face_ids"] as JArray ?? new JArray();
            JObject seedFace = FindInitializerGeometryFace(geometry,
                seedFaceIds.Values<string>().FirstOrDefault(id =>
                {
                    JObject row = FindInitializerGeometryFace(geometry, id);
                    return row != null && row.Value<string>("surface_type") == "cylinder";
                }));
            var seedParameters = seedFace == null ? null :
                seedFace["surface_parameters"] as JObject;
            double? seedRadius = seedParameters == null ? null :
                seedParameters.Value<double?>("radius_m");
            var seedAxis = seedParameters == null ? null : seedParameters["axis"] as JArray;
            var seedOrigin = seedParameters == null ? null : seedParameters["origin"] as JArray;
            if (!seedRadius.HasValue || seedAxis == null || seedAxis.Count != 3 ||
                seedOrigin == null || seedOrigin.Count != 3)
            {
                error = "member is not a frozen cylindrical feature group.";
                return false;
            }

            var candidateIds = new HashSet<string>(seedFaceIds.Values<string>(),
                StringComparer.Ordinal);
            if (patternRefs != null && patternRefs["face_ids"] != null)
                candidateIds.UnionWith(patternRefs["face_ids"].Values<string>());
            var matches = new List<Tuple<string, JObject>>();
            foreach (string faceId in candidateIds)
            {
                JObject face = FindInitializerGeometryFace(geometry, faceId);
                var parameters = face == null ? null : face["surface_parameters"] as JObject;
                var axis = parameters == null ? null : parameters["axis"] as JArray;
                double? radius = parameters == null ? null :
                    parameters.Value<double?>("radius_m");
                if (face == null || face.Value<string>("surface_type") != "cylinder" ||
                    !radius.HasValue || axis == null || axis.Count != 3) continue;
                if (Math.Abs(radius.Value - seedRadius.Value) > 1e-9) continue;
                matches.Add(Tuple.Create(faceId, face));
            }
            int expectedActive = expectedOccurrences - skippedIndices.Count;
            List<Tuple<string, JObject>> transformedOrder;
            if (skippedIndices.Count == 0 && instanceTransforms != null &&
                instanceTransforms.Count == expectedOccurrences &&
                TryOrderInitializerPatternCylinders(matches, seedOrigin, seedAxis,
                    instanceTransforms, out transformedOrder))
                matches = transformedOrder;
            else
            {
                matches = matches.Where(match =>
                {
                    var parameters = match.Item2["surface_parameters"] as JObject;
                    var axis = parameters == null ? null : parameters["axis"] as JArray;
                    if (axis == null || axis.Count != 3) return false;
                    double dot = axis[0].Value<double>() * seedAxis[0].Value<double>() +
                        axis[1].Value<double>() * seedAxis[1].Value<double>() +
                        axis[2].Value<double>() * seedAxis[2].Value<double>();
                    return Math.Abs(Math.Abs(dot) - 1.0) <= 1e-6;
                }).OrderBy(row => row.Item1, StringComparer.Ordinal).ToList();
            }
            if (expectedActive < 1 || matches.Count != expectedActive)
            {
                error = "expected " + expectedActive.ToString(CultureInfo.InvariantCulture) +
                    " active occurrences from FeatureData but matched " +
                    matches.Count.ToString(CultureInfo.InvariantCulture) +
                    " frozen cylindrical faces.";
                return false;
            }

            var occurrences = new JArray();
            int ordinal = 1;
            foreach (Tuple<string, JObject> match in matches)
            {
                string faceId = match.Item1;
                string bodyId = faceId.Substring(0, faceId.IndexOf('F'));
                occurrences.Add(new JObject
                {
                    ["occurrence_id"] = "OCC-" + member.Value<string>("feature_id") + "-" +
                        ordinal.ToString("D4", CultureInfo.InvariantCulture),
                    ["suppressed"] = false,
                    ["geometry_refs"] = new JObject
                    {
                        ["body_ids"] = new JArray(bodyId),
                        ["face_ids"] = new JArray(faceId),
                        ["edge_ids"] = new JArray(match.Item2["edge_ids"] == null ?
                            Enumerable.Empty<string>() : match.Item2["edge_ids"].Values<string>()),
                        ["vertex_ids"] = new JArray()
                    }
                });
                ordinal++;
            }
            foreach (int skipped in skippedIndices.OrderBy(value => value))
                occurrences.Add(new JObject
                {
                    ["occurrence_id"] = "OCC-" + member.Value<string>("feature_id") +
                        "-SUPPRESSED-" + skipped.ToString("D4", CultureInfo.InvariantCulture),
                    ["suppressed"] = true,
                    ["geometry_refs"] = new JObject
                    {
                        ["body_ids"] = new JArray(), ["face_ids"] = new JArray(),
                        ["edge_ids"] = new JArray(), ["vertex_ids"] = new JArray()
                    }
                });
            member["occurrences"] = occurrences;
            member["opening_count"] = expectedActive;
            return true;
        }

        private static bool TryOrderInitializerPatternCylinders(
            IList<Tuple<string, JObject>> candidates, JArray seedOrigin, JArray seedAxis,
            IList<double[]> transforms, out List<Tuple<string, JObject>> ordered)
        {
            ordered = null;
            foreach (bool columnMajor in new[] { true, false })
            {
                var attempt = new List<Tuple<string, JObject>>();
                bool valid = true;
                foreach (double[] transform in transforms)
                {
                    double sx = seedAxis[0].Value<double>();
                    double sy = seedAxis[1].Value<double>();
                    double sz = seedAxis[2].Value<double>();
                    double x = columnMajor ? transform[0] * sx + transform[3] * sy +
                        transform[6] * sz : transform[0] * sx + transform[1] * sy +
                        transform[2] * sz;
                    double y = columnMajor ? transform[1] * sx + transform[4] * sy +
                        transform[7] * sz : transform[3] * sx + transform[4] * sy +
                        transform[5] * sz;
                    double z = columnMajor ? transform[2] * sx + transform[5] * sy +
                        transform[8] * sz : transform[6] * sx + transform[7] * sy +
                        transform[8] * sz;
                    JArray expected = NormalizeInitializerVector(x, y, z);
                    double ox = seedOrigin[0].Value<double>();
                    double oy = seedOrigin[1].Value<double>();
                    double oz = seedOrigin[2].Value<double>();
                    double expectedX = (columnMajor ? transform[0] * ox +
                        transform[3] * oy + transform[6] * oz : transform[0] * ox +
                        transform[1] * oy + transform[2] * oz) + transform[9];
                    double expectedY = (columnMajor ? transform[1] * ox +
                        transform[4] * oy + transform[7] * oz : transform[3] * ox +
                        transform[4] * oy + transform[5] * oz) + transform[10];
                    double expectedZ = (columnMajor ? transform[2] * ox +
                        transform[5] * oy + transform[8] * oz : transform[6] * ox +
                        transform[7] * oy + transform[8] * oz) + transform[11];
                    var matches = candidates.Where(candidate =>
                    {
                        if (attempt.Contains(candidate)) return false;
                        var parameters = candidate.Item2["surface_parameters"] as JObject;
                        var axis = parameters == null ? null : parameters["axis"] as JArray;
                        var origin = parameters == null ? null : parameters["origin"] as JArray;
                        if (axis == null || axis.Count != 3 || origin == null ||
                            origin.Count != 3 || expected == null) return false;
                        double dot = axis[0].Value<double>() * expected[0].Value<double>() +
                            axis[1].Value<double>() * expected[1].Value<double>() +
                            axis[2].Value<double>() * expected[2].Value<double>();
                        if (Math.Abs(Math.Abs(dot) - 1.0) > 1e-6) return false;
                        double px = expectedX - origin[0].Value<double>();
                        double py = expectedY - origin[1].Value<double>();
                        double pz = expectedZ - origin[2].Value<double>();
                        double projection = px * axis[0].Value<double>() +
                            py * axis[1].Value<double>() + pz * axis[2].Value<double>();
                        double dx = px - projection * axis[0].Value<double>();
                        double dy = py - projection * axis[1].Value<double>();
                        double dz = pz - projection * axis[2].Value<double>();
                        return Math.Sqrt(dx * dx + dy * dy + dz * dz) <= 1e-7;
                    }).ToList();
                    if (matches.Count != 1) { valid = false; break; }
                    attempt.Add(matches[0]);
                }
                if (valid && attempt.Count == transforms.Count)
                {
                    ordered = attempt;
                    return true;
                }
            }
            return false;
        }

        private static JObject FindInitializerGeometryFace(JObject geometry, string faceId)
        {
            if (string.IsNullOrWhiteSpace(faceId)) return null;
            foreach (JObject body in (geometry["bodies"] as JArray ?? new JArray())
                .OfType<JObject>())
            {
                JObject face = (body["faces"] as JArray ?? new JArray()).OfType<JObject>()
                    .FirstOrDefault(row => row.Value<string>("id") == faceId);
                if (face != null) return face;
            }
            return null;
        }

        private static JObject FindInitializerGeometryEdge(JObject geometry, string edgeId)
        {
            if (string.IsNullOrWhiteSpace(edgeId)) return null;
            foreach (JObject body in (geometry["bodies"] as JArray ?? new JArray())
                .OfType<JObject>())
            {
                JObject edge = (body["edges"] as JArray ?? new JArray()).OfType<JObject>()
                    .FirstOrDefault(row => row.Value<string>("id") == edgeId);
                if (edge != null) return edge;
            }
            return null;
        }

        private static JObject ReadInitializerLinearPatternTransformAxis(
            ILinearPatternFeatureData pattern)
        {
            try
            {
                if (pattern == null || pattern.D1TotalInstances < 2) return null;
                var first = pattern.GetTransform(0);
                var second = pattern.GetTransform(1);
                double[] a = first != null ? first.ArrayData as double[] : null;
                double[] b = second != null ? second.ArrayData as double[] : null;
                if (a == null || b == null || a.Length < 12 || b.Length < 12) return null;
                double dx = b[9] - a[9];
                double dy = b[10] - a[10];
                double dz = b[11] - a[11];
                JArray direction = NormalizeInitializerVector(dx, dy, dz);
                if (direction == null) return null;
                return new JObject
                {
                    ["origin_m"] = new JArray(R9(a[9]), R9(a[10]), R9(a[11])),
                    ["direction"] = direction
                };
            }
            catch { return null; }
        }

        private static JObject ReadInitializerCircularPatternTransformAxis(
            ICircularPatternFeatureData pattern)
        {
            try
            {
                if (pattern == null || pattern.TotalInstances < 2) return null;
                var first = pattern.GetTransform(0);
                var second = pattern.GetTransform(1);
                double[] a = first != null ? first.ArrayData as double[] : null;
                double[] b = second != null ? second.ArrayData as double[] : null;
                double[] origin;
                double[] direction;
                if (!SemanticFeatureTypeContract.TryReadCircularTransformAxis(a, b,
                    out origin, out direction)) return null;
                return new JObject
                {
                    ["origin_m"] = new JArray(origin.Select(R9)),
                    ["direction"] = new JArray(direction.Select(R9))
                };
            }
            catch { return null; }
        }

        private static JObject ReadInitializerRelationAxis(object axisEntity)
        {
            try
            {
                var feature = axisEntity as IFeature;
                if (feature != null)
                {
                    object specific = feature.GetSpecificFeature2();
                    if (specific != null && !ReferenceEquals(specific, axisEntity))
                    {
                        JObject unwrapped = ReadInitializerRelationAxis(specific);
                        if (unwrapped != null) return unwrapped;
                    }
                }
            }
            catch { }
            double[] values = null;
            try
            {
                var referenceAxis = axisEntity as IRefAxis;
                if (referenceAxis != null) values = referenceAxis.GetRefAxisParams() as double[];
                var edge = axisEntity as IEdge;
                var curve = edge != null ? edge.IGetCurve() : null;
                if (values == null && curve != null && curve.IsLine())
                    values = curve.LineParams as double[];
                var sketchLine = axisEntity as ISketchLine;
                if (values == null && sketchLine != null)
                {
                    var start = sketchLine.IGetStartPoint2();
                    var end = sketchLine.IGetEndPoint2();
                    if (start != null && end != null)
                        values = new[] { start.X, start.Y, start.Z,
                            end.X - start.X, end.Y - start.Y, end.Z - start.Z };
                }
                var sketchSegment = axisEntity as ISketchSegment;
                var segmentCurve = sketchSegment != null ? sketchSegment.IGetCurve() : null;
                if (values == null && segmentCurve != null && segmentCurve.IsLine())
                    values = segmentCurve.LineParams as double[];
                var face = axisEntity as IFace2;
                var surface = face != null ? face.IGetSurface() : null;
                if (values == null && surface != null && surface.IsCylinder())
                {
                    var cylinder = surface.CylinderParams as double[];
                    if (cylinder != null && cylinder.Length >= 6)
                        values = cylinder.Take(6).ToArray();
                }
            }
            catch { }
            if (values == null || values.Length < 6) return null;
            double magnitude = Math.Sqrt(values[3] * values[3] + values[4] * values[4] +
                values[5] * values[5]);
            if (magnitude < 1e-12) return null;
            return new JObject
            {
                ["origin_m"] = new JArray(R9(values[0]), R9(values[1]), R9(values[2])),
                ["direction"] = new JArray(R9(values[3] / magnitude),
                    R9(values[4] / magnitude), R9(values[5] / magnitude))
            };
        }

        private static JArray ReadInitializerPlaneNormal(object planeEntity)
        {
            try
            {
                var feature = planeEntity as IFeature;
                if (feature != null)
                {
                    object specific = feature.GetSpecificFeature2();
                    if (specific != null && !ReferenceEquals(specific, planeEntity))
                    {
                        JArray unwrapped = ReadInitializerPlaneNormal(specific);
                        if (unwrapped != null) return unwrapped;
                    }
                }
                var face = planeEntity as IFace2;
                double[] normal = face != null ? face.Normal as double[] : null;
                if (normal != null && normal.Length >= 3)
                    return NormalizeInitializerVector(normal[0], normal[1], normal[2]);
                var plane = planeEntity as IRefPlane;
                var transform = plane != null ? plane.Transform : null;
                double[] data = transform != null ? transform.ArrayData as double[] : null;
                if (data != null && data.Length >= 9)
                    return NormalizeInitializerVector(data[2], data[5], data[8]);
            }
            catch { }
            return null;
        }

        private static JArray NormalizeInitializerVector(double x, double y, double z)
        {
            double magnitude = Math.Sqrt(x * x + y * y + z * z);
            if (magnitude < 1e-12) return null;
            return new JArray(R9(x / magnitude), R9(y / magnitude), R9(z / magnitude));
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
