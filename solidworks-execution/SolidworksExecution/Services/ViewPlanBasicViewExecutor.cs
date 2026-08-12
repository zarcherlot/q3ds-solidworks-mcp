using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Native executor for basic, C1 section, and C2 local views. The caller owns the surrounding
    /// drawing transaction and must discard the in-memory document if this class returns false.
    /// All capability and reference preflight is completed before the first drawing mutation.
    /// </summary>
    internal sealed class ViewPlanBasicViewExecutor
    {
        private const string ViewNamePrefix = "Q3DS_VP_";
        private const string ManagedAuxiliaryLabelPrefix = "Q3DS_AUX_LABEL_";
        private const double SheetDimensionTolerance = 1e-6;
        private const double PositionTolerance = 5e-7;
        private const double ScaleTolerance = 1e-9;
        private readonly IMathUtility _mathUtility;

        public ViewPlanBasicViewExecutor(ISldWorks solidWorks)
        {
            if (solidWorks == null) throw new ArgumentNullException("solidWorks");
            _mathUtility = solidWorks.IGetMathUtility();
            if (_mathUtility == null)
                throw new InvalidOperationException("SolidWorks math utility is unavailable.");
        }

        public bool TryCreate(IModelDoc2 drawingModel, IDrawingDoc drawing, IModelDoc2 sourceModel,
            ViewPlanBasicExecutionPlan plan, bool sourceModelOwnedByTransaction,
            out ViewPlanBasicViewExecutionResult result, out ViewPlanBasicViewExecutionError error)
        {
            result = null;
            error = null;
            if (drawingModel == null || drawing == null || sourceModel == null || plan == null)
                return Fail("VIEW_PLAN_EXECUTION_CONTEXT_INVALID", null,
                    "Drawing, source model, and compiled ViewPlan are required.", out error);
            if (sourceModel.GetSaveFlag())
                return Fail("VIEW_PLAN_MODEL_HAS_UNSAVED_CHANGES", null,
                    "The source model has unsaved changes; execution is refused.", out error);
            if (!PathEquals(sourceModel.GetPathName(), plan.ModelPath))
                return Fail("VIEW_PLAN_MODEL_BINDING_MISMATCH", null,
                    "The opened source model does not match model_path.", out error);
            if (sourceModel.GetConfigurationByName(plan.Configuration) == null)
                return Fail("VIEW_PLAN_CONFIGURATION_NOT_FOUND", null,
                    "Configuration '" + plan.Configuration + "' does not exist in the source model.",
                    out error);
            if (!DisplayStateExists(sourceModel, plan.Configuration, plan.DisplayState))
                return Fail("VIEW_PLAN_DISPLAY_STATE_NOT_FOUND", null,
                    "Display state '" + plan.DisplayState + "' does not exist in configuration '" +
                    plan.Configuration + "'.", out error);
            bool hasExplicitBasis = plan.Views.Any(item =>
                item.OrientationKind == "explicit_basis");
            if (hasExplicitBasis && !sourceModelOwnedByTransaction)
                return Fail("VIEW_PLAN_EXPLICIT_SOURCE_NOT_ISOLATED", null,
                    "explicit_basis requires a source model opened and owned by this transaction.",
                    out error);
            if (hasExplicitBasis && !sourceModel.IsOpenedReadOnly())
                return Fail("VIEW_PLAN_EXPLICIT_SOURCE_NOT_READ_ONLY", null,
                    "explicit_basis requires the isolated source model to be opened read-only.",
                    out error);
            if (ContainsModelViews(drawing))
                return Fail("VIEW_PLAN_DRAWING_NOT_EMPTY", null,
                    "The drawing must contain no model views before B2 execution.", out error);

            Dictionary<string, string> standardNames;
            HashSet<string> allNames;
            if (!TryResolveModelViewNames(sourceModel, out standardNames, out allNames,
                out string nameError))
                return Fail("VIEW_PLAN_ORIENTATION_CATALOG_UNAVAILABLE", null, nameError, out error);

            var resolvedOrientations = new Dictionary<string, string>(StringComparer.Ordinal);
            var transientNames = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (ViewPlanBasicViewSpec spec in plan.Views)
            {
                if (!IsModelOrientationType(spec.Type)) continue;
                string name;
                if (spec.OrientationKind == "standard_model_view")
                {
                    if (!standardNames.TryGetValue(spec.OrientationName, out name))
                        return Fail("VIEW_PLAN_STANDARD_ORIENTATION_NOT_FOUND", spec.Id,
                            "SolidWorks did not expose standard orientation '" +
                            spec.OrientationName + "'.", out error);
                }
                else if (spec.OrientationKind == "named_model_view")
                {
                    name = allNames.FirstOrDefault(item =>
                        string.Equals(item, spec.OrientationName, StringComparison.Ordinal));
                    if (name == null)
                        return Fail("VIEW_PLAN_NAMED_ORIENTATION_NOT_FOUND", spec.Id,
                            "The exact named model view does not exist: '" +
                            spec.OrientationName + "'.", out error);
                }
                else if (spec.OrientationKind == "explicit_basis")
                {
                    name = BuildTransientViewName(plan, spec);
                    if (allNames.Contains(name))
                        return Fail("VIEW_PLAN_TRANSIENT_NAME_COLLISION", spec.Id,
                            "The deterministic temporary model-view name already exists.", out error);
                    transientNames.Add(spec.Id, name);
                    continue;
                }
                else
                {
                    return Fail("VIEW_PLAN_ORIENTATION_UNSUPPORTED", spec.Id,
                        "The compiled orientation cannot be executed by B2.", out error);
                }
                resolvedOrientations.Add(spec.Id, name);
            }

            var sheet = drawing.GetCurrentSheet() as ISheet;
            if (!TryConfigureSheet(drawingModel, sheet, plan, out string sheetError))
                return Fail("VIEW_PLAN_SHEET_BINDING_MISMATCH", null, sheetError, out error);
            if (!TryConfigureCenterElementDocumentPolicy(drawingModel, plan,
                out string centerPolicyError))
                return Fail("VIEW_PLAN_CENTER_DOCUMENT_POLICY_FAILED", null,
                    centerPolicyError, out error);

            var created = new Dictionary<string, IView>(StringComparer.Ordinal);
            var handles = new Dictionary<string, string>(StringComparer.Ordinal);
            var sectionFingerprints = new Dictionary<string, JObject>(StringComparer.Ordinal);
            var c2Fingerprints = new Dictionary<string, JObject>(StringComparer.Ordinal);
            var auxiliaryFingerprints = new Dictionary<string, JObject>(StringComparer.Ordinal);
            var centerElementFingerprints = new Dictionary<string, JObject>(StringComparer.Ordinal);
            var rows = new JArray();
            foreach (ViewPlanBasicViewSpec spec in plan.Views)
            {
                IView view;
                if (!TryCreateOne(drawingModel, drawing, sourceModel, spec, created,
                    resolvedOrientations, transientNames, out view, out string createError))
                    return Fail("VIEW_PLAN_VIEW_CREATION_FAILED", spec.Id, createError, out error);
                if (!TryApplyContract(drawingModel, drawing, view, spec, plan,
                    out string applyError))
                    return Fail("VIEW_PLAN_VIEW_CONFIGURATION_FAILED", spec.Id, applyError, out error);
                if (spec.Type == "broken_out_section" &&
                    !TryCreateBrokenOut(drawingModel, drawing, view, spec,
                        out string brokenOutError))
                    return Fail("VIEW_PLAN_BROKEN_OUT_CREATION_FAILED", spec.Id,
                        brokenOutError, out error);
                if (!TryCreateCenterElements(drawingModel, drawing, view, spec,
                    out string centerError))
                    return Fail("VIEW_PLAN_CENTER_ELEMENT_CREATION_FAILED", spec.Id,
                        centerError, out error);

                string handle = SafeUniqueName(view);
                if (string.IsNullOrEmpty(handle))
                    return Fail("VIEW_PLAN_VIEW_HANDLE_UNAVAILABLE", spec.Id,
                        "SolidWorks did not provide a persistent unique view handle.", out error);
                created.Add(spec.Id, view);
                handles.Add(spec.Id, handle);

                JObject row;
                if (!TryReadAndVerify(drawingModel, view, spec, plan, created, out row,
                    out string verificationError))
                    return Fail("VIEW_PLAN_IN_MEMORY_READBACK_FAILED", spec.Id,
                        verificationError, out error);
                rows.Add(row);
                if (IsSectionType(spec.Type))
                    sectionFingerprints.Add(spec.Id, (JObject)row["section"].DeepClone());
                if (IsC2Type(spec.Type))
                    c2Fingerprints.Add(spec.Id, (JObject)row["c2"].DeepClone());
                if (spec.Type == "auxiliary_view")
                    auxiliaryFingerprints.Add(spec.Id,
                        (JObject)row["auxiliary"].DeepClone());
                if (spec.CenterMarks.Count > 0 || spec.SymmetryCenterlines.Count > 0)
                    centerElementFingerprints.Add(spec.Id,
                        (JObject)row["center_elements"].DeepClone());
            }

            // Inserting the first model view can make SolidWorks reapply the template's sheet
            // scale even though the earlier SetProperties2/SetScale call read back correctly.
            // Reassert the frozen sheet contract only after all views exist, then verify it before
            // handing control to the disk transaction.
            if (!TryConfigureSheet(drawingModel, sheet, plan, out sheetError))
                return Fail("VIEW_PLAN_SHEET_FINALIZATION_FAILED", null, sheetError, out error);
            if (!TryVerifySheetContract(sheet, plan, out sheetError))
                return Fail("VIEW_PLAN_SHEET_FINALIZATION_FAILED", null, sheetError, out error);

            result = new ViewPlanBasicViewExecutionResult
            {
                CreatedViews = created,
                PersistentHandles = handles,
                SectionFingerprints = sectionFingerprints,
                C2Fingerprints = c2Fingerprints,
                AuxiliaryFingerprints = auxiliaryFingerprints,
                CenterElementFingerprints = centerElementFingerprints,
                InMemoryReadback = new JObject
                {
                    ["verified"] = true,
                    ["view_count"] = created.Count,
                    ["views"] = rows
                }
            };
            return true;
        }

        public bool TryVerifyPersisted(IDrawingDoc drawing, ViewPlanBasicExecutionPlan plan,
            IDictionary<string, string> expectedHandles,
            IDictionary<string, JObject> expectedSectionFingerprints,
            IDictionary<string, JObject> expectedC2Fingerprints,
            IDictionary<string, JObject> expectedAuxiliaryFingerprints,
            IDictionary<string, JObject> expectedCenterElementFingerprints,
            out JObject snapshot,
            out ViewPlanBasicViewExecutionError error)
        {
            snapshot = new JObject { ["verified"] = false, ["views"] = new JArray() };
            error = null;
            if (drawing == null || plan == null || expectedHandles == null)
                return Fail("VIEW_PLAN_VERIFICATION_CONTEXT_INVALID", null,
                    "Drawing, compiled plan, and expected handles are required.", out error);
            if (!TryVerifySheetContract(drawing.GetCurrentSheet() as ISheet, plan,
                out string sheetError))
                return Fail("VIEW_PLAN_PERSISTED_SHEET_MISMATCH", null, sheetError, out error);
            if (!TryVerifyCenterElementDocumentPolicy(drawing as IModelDoc2, plan,
                out string centerPolicyError))
                return Fail("VIEW_PLAN_PERSISTED_CENTER_POLICY_MISMATCH", null,
                    centerPolicyError, out error);

            var liveViews = new List<IView>();
            object current = drawing.GetFirstView();
            int guard = 0;
            while (current != null && guard++ < 256)
            {
                var live = current as IView;
                object next = live != null ? live.GetNextView() : null;
                if (live != null && live.Type != 1) liveViews.Add(live);
                current = next;
            }
            if (liveViews.Count != plan.Views.Count)
                return Fail("VIEW_PLAN_PERSISTED_VIEW_COUNT_MISMATCH", null,
                    "Expected " + plan.Views.Count + " model views but reopened drawing contains " +
                    liveViews.Count + ".", out error);

            var matched = new Dictionary<string, IView>(StringComparer.Ordinal);
            var used = new HashSet<string>(StringComparer.Ordinal);
            var rows = (JArray)snapshot["views"];
            foreach (ViewPlanBasicViewSpec spec in plan.Views)
            {
                string expectedHandle;
                if (!expectedHandles.TryGetValue(spec.Id, out expectedHandle) ||
                    string.IsNullOrEmpty(expectedHandle))
                    return Fail("VIEW_PLAN_EXPECTED_HANDLE_MISSING", spec.Id,
                        "No expected persistent handle was recorded.", out error);
                IView live = liveViews.FirstOrDefault(item => string.Equals(
                    SafeUniqueName(item), expectedHandle, StringComparison.Ordinal));
                if (live == null)
                    live = liveViews.FirstOrDefault(item => string.Equals(item.Name,
                        ViewNamePrefix + spec.Id, StringComparison.Ordinal));
                if (live == null)
                    return Fail("VIEW_PLAN_PERSISTED_VIEW_MISSING", spec.Id,
                        "View was not found by persistent handle or deterministic name.", out error);
                string actualHandle = SafeUniqueName(live);
                if (!string.Equals(actualHandle, expectedHandle, StringComparison.Ordinal))
                    return Fail("VIEW_PLAN_PERSISTED_HANDLE_MISMATCH", spec.Id,
                        "Persistent unique handle changed after save/reopen.", out error);
                if (!used.Add(actualHandle))
                    return Fail("VIEW_PLAN_PERSISTED_HANDLE_DUPLICATE", spec.Id,
                        "Multiple planned views resolved to the same persistent handle.", out error);
                matched.Add(spec.Id, live);
                JObject row;
                if (!TryReadAndVerify(drawing as IModelDoc2, live, spec, plan, matched, out row,
                    out string verificationError))
                    return Fail("VIEW_PLAN_PERSISTED_VIEW_MISMATCH", spec.Id,
                        verificationError, out error);
                if (IsSectionType(spec.Type) && expectedSectionFingerprints != null)
                {
                    JObject expectedFingerprint;
                    if (!expectedSectionFingerprints.TryGetValue(spec.Id,
                        out expectedFingerprint) || expectedFingerprint == null)
                        return Fail("VIEW_PLAN_SECTION_FINGERPRINT_MISSING", spec.Id,
                            "No in-memory section fingerprint was recorded before save.", out error);
                    if (!JToken.DeepEquals(expectedFingerprint, row["section"]))
                        return Fail("VIEW_PLAN_SECTION_FINGERPRINT_CHANGED", spec.Id,
                            "Normalized section geometry or semantics changed after save/reopen.",
                            out error);
                }
                if (IsC2Type(spec.Type) && expectedC2Fingerprints != null)
                {
                    JObject expectedFingerprint;
                    if (!expectedC2Fingerprints.TryGetValue(spec.Id,
                        out expectedFingerprint) || expectedFingerprint == null)
                        return Fail("VIEW_PLAN_C2_FINGERPRINT_MISSING", spec.Id,
                            "No in-memory C2 fingerprint was recorded before save.", out error);
                    if (!JToken.DeepEquals(expectedFingerprint, row["c2"]))
                        return Fail("VIEW_PLAN_C2_FINGERPRINT_CHANGED", spec.Id,
                            "Normalized C2 geometry or semantics changed after save/reopen.",
                            out error);
                }
                if (spec.Type == "auxiliary_view" &&
                    expectedAuxiliaryFingerprints != null)
                {
                    JObject expectedFingerprint;
                    if (!expectedAuxiliaryFingerprints.TryGetValue(spec.Id,
                        out expectedFingerprint) || expectedFingerprint == null)
                        return Fail("VIEW_PLAN_AUXILIARY_FINGERPRINT_MISSING", spec.Id,
                            "No in-memory auxiliary fingerprint was recorded before save.",
                            out error);
                    if (!JToken.DeepEquals(expectedFingerprint, row["auxiliary"]))
                        return Fail("VIEW_PLAN_AUXILIARY_FINGERPRINT_CHANGED", spec.Id,
                            "Normalized auxiliary geometry or semantics changed after " +
                            "save/reopen.", out error);
                }
                if ((spec.CenterMarks.Count > 0 || spec.SymmetryCenterlines.Count > 0) &&
                    expectedCenterElementFingerprints != null)
                {
                    JObject expectedFingerprint;
                    if (!expectedCenterElementFingerprints.TryGetValue(spec.Id,
                        out expectedFingerprint) || expectedFingerprint == null)
                        return Fail("VIEW_PLAN_CENTER_FINGERPRINT_MISSING", spec.Id,
                            "No in-memory center-element fingerprint was recorded before save.",
                            out error);
                    if (!JToken.DeepEquals(expectedFingerprint, row["center_elements"]))
                        return Fail("VIEW_PLAN_CENTER_FINGERPRINT_CHANGED", spec.Id,
                            "Normalized center-element geometry changed after save/reopen.",
                            out error);
                }
                rows.Add(row);
            }
            snapshot["view_count"] = matched.Count;
            snapshot["verified"] = true;
            return true;
        }

        private bool TryCreateOne(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IModelDoc2 sourceModel, ViewPlanBasicViewSpec spec,
            Dictionary<string, IView> created, Dictionary<string, string> resolvedOrientations,
            Dictionary<string, string> transientNames, out IView view, out string error)
        {
            view = null;
            error = null;
            drawingModel.ClearSelection2(true);
            if (IsModelOrientationType(spec.Type))
            {
                if (spec.OrientationKind == "explicit_basis")
                {
                    if (!TryCreateExplicitModelView(drawing, sourceModel, spec,
                        transientNames[spec.Id], out view, out error)) return false;
                }
                else
                {
                    view = drawing.CreateDrawViewFromModelView3(sourceModel.GetPathName(),
                        resolvedOrientations[spec.Id], spec.X, spec.Y, 0.0) as IView;
                }
            }
            else
            {
                if (!created.TryGetValue(spec.ParentId, out IView parent))
                {
                    error = "Parent view '" + spec.ParentId + "' was not created.";
                    return false;
                }
                if (spec.Type == "projected_view")
                {
                    if (!drawing.ActivateView(parent.Name))
                    {
                        error = "The unique parent drawing view could not be activated.";
                        return false;
                    }
                    double[] parentPosition = parent.Position as double[];
                    double x = parentPosition != null && parentPosition.Length >= 2
                        ? parentPosition[0] : 0.0;
                    double y = parentPosition != null && parentPosition.Length >= 2
                        ? parentPosition[1] : 0.0;
                    bool selected = drawingModel.Extension.SelectByID2(parent.Name, "DRAWINGVIEW",
                        x, y, 0.0, false, 0, null, 0);
                    if (!selected)
                    {
                        error = "The unique parent drawing view could not be selected.";
                        return false;
                    }
                    view = drawing.CreateUnfoldedViewAt3(spec.X, spec.Y, 0.0, false) as IView;
                }
                else if (IsSectionType(spec.Type))
                {
                    if (!TryCreateSectionView(drawingModel, drawing, parent, spec,
                        out view, out error)) return false;
                }
                else if (spec.Type == "detail_view")
                {
                    if (!TryCreateDetailView(drawingModel, drawing, parent, spec,
                        out view, out error)) return false;
                }
                else if (spec.Type == "auxiliary_view")
                {
                    if (!TryCreateAuxiliaryView(drawingModel, drawing, parent, spec,
                        out view, out error)) return false;
                }
                else
                {
                    error = "The compiled parent-derived view type is unsupported.";
                    return false;
                }
            }
            drawingModel.ClearSelection2(true);
            if (view == null)
            {
                error = "The native SolidWorks creation API returned null.";
                return false;
            }
            if (!UsesNativeGeneratedName(spec.Type) &&
                !view.SetName2(ViewNamePrefix + spec.Id))
            {
                error = "The created view could not receive its deterministic name.";
                return false;
            }
            if (UsesNativeGeneratedName(spec.Type) &&
                string.IsNullOrWhiteSpace(SafeUniqueName(view)))
            {
                error = "The created native-named view did not expose a persistent unique " +
                    "handle.";
                return false;
            }
            return true;
        }

        private static bool TryCreateSectionView(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView parent, ViewPlanBasicViewSpec spec, out IView sectionView, out string error)
        {
            sectionView = null;
            error = null;
            if (!drawing.ActivateView(parent.Name))
            {
                error = "The unique section parent view could not be activated.";
                return false;
            }
            IList<double[]> points;
            if (!TryResolveSectionPoints(parent, spec, out points, out error)) return false;
            var segments = new List<ISketchSegment>();
            try
            {
                drawingModel.ClearSelection2(true);
                ISketchManager manager = drawingModel.SketchManager;
                bool previousAddToDb = manager.AddToDB;
                bool previousDisplay = manager.DisplayWhenAdded;
                try
                {
                    // A frozen ViewPlan line is already fully constrained in model space.
                    // AddToDB bypasses grid snapping and sketch inference so SolidWorks cannot
                    // move a bend point or flatten an angled leg while the segments are added.
                    manager.AddToDB = true;
                    manager.DisplayWhenAdded = false;
                    for (int index = 0; index + 1 < points.Count; index++)
                    {
                        double[] first = points[index];
                        double[] second = points[index + 1];
                        var segment = manager.CreateLine(first[0], first[1], first[2],
                            second[0], second[1], second[2]) as ISketchSegment;
                        if (segment == null)
                        {
                            error = "SolidWorks did not create cutting-line segment " + index + ".";
                            return false;
                        }
                        segments.Add(segment);
                    }
                }
                finally
                {
                    manager.DisplayWhenAdded = previousDisplay;
                    manager.AddToDB = previousAddToDb;
                }
                if (!TryVerifyCreatedSectionSegments(segments, points, out error)) return false;
                drawingModel.ClearSelection2(true);
                foreach (ISketchSegment segment in segments)
                    if (!segment.Select4(true, null))
                    {
                        error = "A unique cutting-line segment could not be selected.";
                        return false;
                    }
                int options = SectionOptions(spec);
                sectionView = drawing.CreateSectionViewAt5(spec.X, spec.Y, 0.0,
                    spec.SectionLabel, options, null, spec.SectionDepth) as IView;
                if (sectionView == null)
                {
                    error = "CreateSectionViewAt5 returned null.";
                    return false;
                }
                var data = sectionView.GetSection() as IDrSection;
                if (data == null)
                {
                    error = "The created section view has no IDrSection contract.";
                    return false;
                }
                int labelStatus = data.SetLabel2(spec.SectionLabel);
                if (labelStatus < 0 || !string.Equals(data.GetLabel(), spec.SectionLabel,
                    StringComparison.Ordinal))
                {
                    error = "SolidWorks rejected the exact frozen section label.";
                    return false;
                }
                data.SetPartialSection(spec.Type == "half_section");
                data.SetReversedCutDirection(spec.SectionReverseDirection);
                data.SetScaleWithModelChanges(false);
                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
            finally
            {
                drawingModel.ClearSelection2(true);
            }
        }

        private static bool TryVerifyCreatedSectionSegments(IList<ISketchSegment> segments,
            IList<double[]> points, out string error)
        {
            error = null;
            if (segments == null || points == null || segments.Count + 1 != points.Count)
                return FailMessage("Created cutting-line topology differs from the frozen points.",
                    out error);
            for (int index = 0; index < segments.Count; index++)
            {
                var line = segments[index] as ISketchLine;
                var start = line != null ? line.IGetStartPoint2() : null;
                var end = line != null ? line.IGetEndPoint2() : null;
                if (start == null || end == null)
                    return FailMessage("Created cutting-line segment " + index +
                        " has no exact endpoint readback.", out error);
                double[] expectedStart = points[index];
                double[] expectedEnd = points[index + 1];
                if (Math.Abs(start.X - expectedStart[0]) > PositionTolerance ||
                    Math.Abs(start.Y - expectedStart[1]) > PositionTolerance ||
                    Math.Abs(start.Z - expectedStart[2]) > PositionTolerance ||
                    Math.Abs(end.X - expectedEnd[0]) > PositionTolerance ||
                    Math.Abs(end.Y - expectedEnd[1]) > PositionTolerance ||
                    Math.Abs(end.Z - expectedEnd[2]) > PositionTolerance)
                    return FailMessage("SolidWorks changed frozen cutting-line segment " + index +
                        " while creating its sketch geometry.", out error);
            }
            return true;
        }

        private bool TryCreateBrokenOut(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView view, ViewPlanBasicViewSpec spec, out string error)
        {
            error = null;
            if (view.GetBreakOutSectionCount() != 0)
                return FailMessage("The new base view already contains a broken-out section.",
                    out error);
            ISketchSegment profile;
            if (!TryCreateCircularProfile(drawingModel, drawing, view, spec.ProfileOffsetX,
                spec.ProfileOffsetY, spec.ProfileRadiusSheet, out profile, out error))
                return false;
            try
            {
                if (!drawing.CreateBreakOutSection(spec.BrokenOutDepth))
                    return FailMessage("CreateBreakOutSection returned false.", out error);
                drawingModel.ClearSelection2(true);
                drawingModel.ForceRebuild3(false);
                if (view.GetBreakOutSectionCount() != 1)
                    return FailMessage("Broken-out section count did not become exactly one.",
                        out error);
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
            finally
            {
                drawingModel.ClearSelection2(true);
            }
        }

        private bool TryCreateDetailView(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView parent, ViewPlanBasicViewSpec spec, out IView detailView, out string error)
        {
            detailView = null;
            error = null;
            int countBefore = parent.GetDetailCircleCount();
            ISketchSegment profile;
            if (!TryCreateCircularProfile(drawingModel, drawing, parent, spec.ProfileOffsetX,
                spec.ProfileOffsetY, spec.ProfileRadiusSheet, out profile, out error))
                return false;
            try
            {
                detailView = drawing.CreateDetailViewAt4(spec.X, spec.Y, 0.0,
                    spec.DetailStyle, spec.Scale, 1.0, spec.DetailLabel,
                    spec.DetailShowType, spec.DetailFullOutline, spec.DetailJaggedOutline,
                    spec.DetailNoOutline, spec.DetailShapeIntensity) as IView;
                drawingModel.ClearSelection2(true);
                if (detailView == null)
                    return FailMessage("CreateDetailViewAt4 returned null.", out error);
                var data = detailView.GetDetail() as IDetailCircle;
                if (data == null)
                    return FailMessage("The created detail view has no IDetailCircle contract.",
                        out error);
                // These setters report false when the requested value already came from
                // CreateDetailViewAt4, so acceptance is determined only by exact readback.
                data.SetLabel(spec.DetailLabel);
                data.SetStyle(spec.DetailStyle);
                data.SetDisplay(spec.DetailShowType);
                data.SetFullOutline(spec.DetailFullOutline);
                data.JaggedOutline = spec.DetailJaggedOutline;
                data.NoOutline = spec.DetailNoOutline;
                // CreateDetailViewAt4 applies ShapeIntensity exactly. Reassigning the same value
                // through IDetailCircle.ShapeIntensity is not idempotent in SolidWorks 2025 and
                // resets it to the document default, so retain the creation-time value and prove
                // it through the post-rebuild readback below.
                if (spec.DetailLabelPositionMode == "explicit")
                {
                    // SolidWorks constrains a standard detail label to the detail-profile
                    // perimeter: SetLabelPosition uses the supplied point as a direction,
                    // while GetLabelPosition returns the persisted label anchor.  Invert that
                    // angular mapping through bounded native readback and retain the planned
                    // sheet coordinate as the only acceptance criterion.
                    double[] parentPosition = parent.Position as double[];
                    if (parentPosition == null || parentPosition.Length < 2)
                        return FailMessage("Detail parent position is unavailable for explicit " +
                            "label positioning.", out error);
                    double centerX = parentPosition[0] + spec.ProfileOffsetX;
                    double centerY = parentPosition[1] + spec.ProfileOffsetY;
                    double targetDx = spec.DetailLabelX.Value - centerX;
                    double targetDy = spec.DetailLabelY.Value - centerY;
                    double controlAngle = Math.Atan2(targetDy, targetDx);
                    double handleRadius = Math.Max(0.02, spec.ProfileRadiusSheet * 4.0);
                    double inputX = centerX + handleRadius * Math.Cos(controlAngle);
                    double inputY = centerY + handleRadius * Math.Sin(controlAngle);
                    for (int attempt = 0; attempt < 8; attempt++)
                    {
                        data.SetLabelPosition(inputX, inputY);
                        drawingModel.ForceRebuild3(false);
                        double actualX = 0.0;
                        double actualY = 0.0;
                        data.GetLabelPosition(out actualX, out actualY);
                        double errorX = spec.DetailLabelX.Value - actualX;
                        double errorY = spec.DetailLabelY.Value - actualY;
                        if (Math.Abs(errorX) <= PositionTolerance &&
                            Math.Abs(errorY) <= PositionTolerance) break;
                        double actualDx = actualX - centerX;
                        double actualDy = actualY - centerY;
                        if (targetDx * targetDx + targetDy * targetDy <=
                                PositionTolerance * PositionTolerance ||
                            actualDx * actualDx + actualDy * actualDy <=
                                PositionTolerance * PositionTolerance) break;
                        double targetAngle = Math.Atan2(targetDy, targetDx);
                        double actualAngle = Math.Atan2(actualDy, actualDx);
                        controlAngle += Math.Atan2(Math.Sin(targetAngle - actualAngle),
                            Math.Cos(targetAngle - actualAngle));
                        inputX = centerX + handleRadius * Math.Cos(controlAngle);
                        inputY = centerY + handleRadius * Math.Sin(controlAngle);
                    }
                }
                drawingModel.ForceRebuild3(false);
                if (!string.Equals(data.GetLabel(), spec.DetailLabel, StringComparison.Ordinal) ||
                    data.GetStyle() != spec.DetailStyle ||
                    data.GetDisplay() != spec.DetailShowType ||
                    data.HasFullOutline() != spec.DetailFullOutline ||
                    data.JaggedOutline != spec.DetailJaggedOutline ||
                    data.NoOutline != spec.DetailNoOutline ||
                    (spec.DetailJaggedOutline &&
                     data.ShapeIntensity != spec.DetailShapeIntensity))
                    return FailMessage(string.Format(CultureInfo.InvariantCulture,
                        "SolidWorks detail property readback differs immediately after creation: " +
                        "label={0}/{1}, style={2}/{3}, display={4}/{5}, full={6}/{7}, " +
                        "jagged={8}/{9}, none={10}/{11}, intensity={12}/{13}.",
                        data.GetLabel(), spec.DetailLabel, data.GetStyle(), spec.DetailStyle,
                        data.GetDisplay(), spec.DetailShowType, data.HasFullOutline(),
                        spec.DetailFullOutline, data.JaggedOutline, spec.DetailJaggedOutline,
                        data.NoOutline, spec.DetailNoOutline, data.ShapeIntensity,
                        spec.DetailShapeIntensity), out error);
                if (parent.GetDetailCircleCount() != countBefore + 1)
                    return FailMessage("Parent detail-circle count did not increase by one.",
                        out error);
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
            finally
            {
                drawingModel.ClearSelection2(true);
            }
        }

        private bool TryCreateAuxiliaryView(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView parent, ViewPlanBasicViewSpec spec, out IView auxiliaryView,
            out string error)
        {
            auxiliaryView = null;
            error = null;
            if (!drawing.ActivateView(parent.Name))
                return FailMessage("The unique auxiliary parent view could not be activated.",
                    out error);
            IEdge referenceEdge;
            double[] ignoredExpectedStart;
            double[] ignoredExpectedEnd;
            double[] ignoredActualStart;
            double[] ignoredActualEnd;
            if (!TryResolveAuxiliaryReferenceEdge(parent, spec, out referenceEdge,
                out ignoredExpectedStart, out ignoredExpectedEnd, out ignoredActualStart,
                out ignoredActualEnd, out error)) return false;
            try
            {
                drawingModel.ClearSelection2(true);
                if (!parent.SelectEntity(referenceEdge, false))
                    return FailMessage("The frozen auxiliary reference edge could not be " +
                        "selected in its unique parent view.", out error);
                auxiliaryView = drawing.CreateAuxiliaryViewAt2(spec.X, spec.Y, 0.0,
                    spec.AuxiliaryNotAligned, spec.AuxiliaryLabel,
                    spec.AuxiliaryShowArrow, spec.AuxiliaryFlip) as IView;
                drawingModel.ClearSelection2(true);
                if (auxiliaryView == null)
                    return FailMessage("CreateAuxiliaryViewAt2 returned null.", out error);
                drawingModel.ForceRebuild3(false);
                if (spec.AuxiliaryLabelPositionMode == "explicit" &&
                    !TryCreateManagedAuxiliaryLabel(drawingModel, drawing, parent,
                        auxiliaryView, spec, out error))
                    return false;
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
            finally
            {
                drawingModel.ClearSelection2(true);
            }
        }

        private static bool TryCreateManagedAuxiliaryLabel(IModelDoc2 drawingModel,
            IDrawingDoc drawing, IView parent, IView auxiliary,
            ViewPlanBasicViewSpec spec, out string error)
        {
            error = null;
            try
            {
                var arrow = auxiliary.GetProjectionArrow() as IProjectionArrow;
                if (arrow == null)
                    return FailMessage("Auxiliary projection arrow is unavailable for " +
                        "explicit label creation.", out error);
                if (!string.Equals(arrow.GetLabel(), spec.AuxiliaryLabel,
                    StringComparison.Ordinal))
                    return FailMessage("Native auxiliary label differs before explicit " +
                        "label replacement.", out error);
                var nativeFormat = arrow.GetTextFormat() as ITextFormat;
                if (nativeFormat == null)
                    return FailMessage("Native auxiliary arrow text format is unavailable.",
                        out error);
                if (!arrow.SetLabel("") || !string.IsNullOrEmpty(arrow.GetLabel()))
                    return FailMessage("SolidWorks refused to clear the non-positionable " +
                        "native auxiliary label.", out error);
                if (!drawing.ActivateView(parent.Name))
                    return FailMessage("The auxiliary parent view could not be activated for " +
                        "managed label creation.", out error);
                drawingModel.ClearSelection2(true);
                INote note = drawingModel.IInsertNote(spec.AuxiliaryLabel);
                IAnnotation annotation = note != null ? note.IGetAnnotation() : null;
                if (note == null || annotation == null)
                    return FailMessage("SolidWorks did not create the managed auxiliary label " +
                        "note.", out error);
                string expectedName = BuildManagedAuxiliaryLabelName(spec);
                if (!annotation.SetName(expectedName))
                    return FailMessage("SolidWorks refused the deterministic managed " +
                        "auxiliary-label name.", out error);
                if (!annotation.SetTextFormat(0, false, nativeFormat))
                    return FailMessage("SolidWorks refused to copy the native view-arrow text " +
                        "format to the managed auxiliary label.", out error);
                if (!annotation.SetPosition2(spec.AuxiliaryLabelX.Value,
                    spec.AuxiliaryLabelY.Value, 0.0))
                    return FailMessage("SolidWorks refused the explicit auxiliary-label " +
                        "position.", out error);
                drawingModel.ForceRebuild3(false);
                JObject ignored;
                if (!TryReadManagedAuxiliaryLabel(parent, arrow, spec, out ignored,
                    out error)) return false;
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
            finally
            {
                try { drawingModel.ClearSelection2(true); } catch { }
            }
        }

        private static bool TryReadManagedAuxiliaryLabel(IView parent,
            IProjectionArrow arrow, ViewPlanBasicViewSpec spec, out JObject contract,
            out string error)
        {
            contract = new JObject();
            error = null;
            try
            {
                string expectedName = BuildManagedAuxiliaryLabelName(spec);
                IAnnotation[] matches = ManagedAuxiliaryLabelAnnotations(parent, expectedName);
                if (matches.Length != 1)
                    return FailMessage("Expected exactly one parent-view-owned managed " +
                        "auxiliary label named '" + expectedName + "', found " +
                        matches.Length.ToString(CultureInfo.InvariantCulture) + ".", out error);
                IAnnotation annotation = matches[0];
                INote note = annotation.GetSpecificAnnotation() as INote;
                IView owner = annotation.Owner as IView;
                double[] position = annotation.GetPosition() as double[];
                ITextFormat managedFormat = annotation.GetTextFormat(0) as ITextFormat;
                ITextFormat nativeFormat = arrow.GetTextFormat() as ITextFormat;
                if (note == null || owner == null || position == null || position.Length < 2 ||
                    managedFormat == null || nativeFormat == null)
                    return FailMessage("Managed auxiliary label note, owner, position, or text " +
                        "format is unavailable.", out error);
                if (annotation.GetType() != 6 || annotation.OwnerType != 0 ||
                    !string.Equals(SafeUniqueName(owner), SafeUniqueName(parent),
                        StringComparison.Ordinal))
                    return FailMessage("Managed auxiliary label is not uniquely owned by the " +
                        "frozen parent drawing view.", out error);
                if (!string.Equals(note.GetText(), spec.AuxiliaryLabel,
                    StringComparison.Ordinal))
                    return FailMessage("Managed auxiliary label text differs from the frozen " +
                        "label.", out error);
                if (annotation.Visible != 1 || annotation.GetLeader() ||
                    annotation.GetAttachedEntityCount3() != 0)
                    return FailMessage("Managed auxiliary label must be visible, leaderless, " +
                        "and parent-view-owned without attached geometry.", out error);
                if (annotation.GetUseDocTextFormat(0) ||
                    !TextFormatsEquivalent(managedFormat, nativeFormat))
                    return FailMessage("Managed auxiliary label does not preserve the native " +
                        "projection-arrow text format.", out error);
                if (double.IsNaN(position[0]) || double.IsInfinity(position[0]) ||
                    double.IsNaN(position[1]) || double.IsInfinity(position[1]) ||
                    Math.Abs(position[0] - spec.AuxiliaryLabelX.Value) >
                        PositionTolerance ||
                    Math.Abs(position[1] - spec.AuxiliaryLabelY.Value) >
                        PositionTolerance)
                    return FailMessage(string.Format(CultureInfo.InvariantCulture,
                        "Managed auxiliary label position differs from the explicit plan: " +
                        "actual=({0:R},{1:R}), expected=({2:R},{3:R}).", position[0],
                        position[1], spec.AuxiliaryLabelX.Value,
                        spec.AuxiliaryLabelY.Value), out error);
                contract["annotation_name"] = annotation.GetName();
                contract["annotation_type"] = annotation.GetType();
                contract["owner_type"] = annotation.OwnerType;
                contract["owner_unique_name"] = SafeUniqueName(owner);
                contract["visible"] = annotation.Visible;
                contract["leader"] = annotation.GetLeader();
                contract["attached_entity_count"] = annotation.GetAttachedEntityCount3();
                contract["use_document_text_format"] =
                    annotation.GetUseDocTextFormat(0);
                contract["text"] = note.GetText();
                contract["position_sheet_m"] = new JArray(
                    Quantize(position[0]), Quantize(position[1]));
                contract["text_format"] = BuildTextFormatContract(managedFormat);
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static IAnnotation[] ManagedAuxiliaryLabelAnnotations(IView parent,
            string expectedName)
        {
            var annotations = parent != null ? parent.GetAnnotations() as Array : null;
            if (annotations == null) return new IAnnotation[0];
            return annotations.Cast<object>().OfType<IAnnotation>().Where(item =>
            {
                try
                {
                    return string.Equals(item.GetName(), expectedName,
                        StringComparison.Ordinal);
                }
                catch { return false; }
            }).ToArray();
        }

        private static string BuildManagedAuxiliaryLabelName(ViewPlanBasicViewSpec spec)
        {
            return ManagedAuxiliaryLabelPrefix +
                spec.OriginalIndex.ToString(CultureInfo.InvariantCulture);
        }

        private static bool TextFormatsEquivalent(ITextFormat first, ITextFormat second)
        {
            return first != null && second != null && JToken.DeepEquals(
                BuildTextFormatContract(first), BuildTextFormatContract(second));
        }

        private static JObject BuildTextFormatContract(ITextFormat format)
        {
            return new JObject
            {
                ["typeface"] = format.TypeFaceName,
                ["height_specified_in_points"] = format.IsHeightSpecifiedInPts(),
                ["char_height_m"] = Quantize(format.CharHeight),
                ["char_height_points"] = format.CharHeightInPts,
                ["width_factor"] = Quantize(format.WidthFactor),
                ["char_spacing_factor"] = Quantize(format.CharSpacingFactor),
                ["line_spacing"] = Quantize(format.LineSpacing),
                ["line_length"] = Quantize(format.LineLength),
                ["escapement"] = Quantize(format.Escapement),
                ["oblique_angle"] = Quantize(format.ObliqueAngle),
                ["bold"] = format.Bold,
                ["italic"] = format.Italic,
                ["underline"] = format.Underline,
                ["strikeout"] = format.Strikeout,
                ["vertical"] = format.Vertical,
                ["backwards"] = format.BackWards,
                ["upside_down"] = format.UpsideDown
            };
        }

        private static bool TryConfigureCenterElementDocumentPolicy(IModelDoc2 drawingModel,
            ViewPlanBasicExecutionPlan plan, out string error)
        {
            error = null;
            if (drawingModel == null)
                return FailMessage("Drawing model is unavailable for center-element policy.",
                    out error);
            try
            {
                // Prevent template auto-insert preferences from adding unplanned annotations on
                // the first save/reopen. These are document preferences on the new output only.
                if (!drawingModel.SetUserPreferenceToggle(189, false) ||
                    !drawingModel.SetUserPreferenceToggle(190, false))
                    return FailMessage("SolidWorks refused to disable automatic center " +
                        "annotations for the output drawing.", out error);
                ViewPlanCenterMarkSpec defaults = plan.Views
                    .SelectMany(item => item.CenterMarks)
                    .FirstOrDefault(item => item.UseDocumentDefaults);
                if (defaults != null &&
                    !drawingModel.SetUserPreferenceToggle(46, defaults.ShowLines))
                    return FailMessage("SolidWorks refused to freeze the document-default " +
                        "center-mark line setting.", out error);
                return TryVerifyCenterElementDocumentPolicy(drawingModel, plan, out error);
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static bool TryVerifyCenterElementDocumentPolicy(IModelDoc2 drawingModel,
            ViewPlanBasicExecutionPlan plan, out string error)
        {
            error = null;
            if (drawingModel == null)
                return FailMessage("Drawing model is unavailable for center-element readback.",
                    out error);
            try
            {
                if (drawingModel.GetUserPreferenceToggle(189) ||
                    drawingModel.GetUserPreferenceToggle(190))
                    return FailMessage("Automatic center annotations are enabled in the output " +
                        "drawing.", out error);
                ViewPlanCenterMarkSpec defaults = plan.Views
                    .SelectMany(item => item.CenterMarks)
                    .FirstOrDefault(item => item.UseDocumentDefaults);
                if (defaults != null && drawingModel.GetUserPreferenceToggle(46) !=
                    defaults.ShowLines)
                    return FailMessage("Document-default center-mark lines differ from the " +
                        "frozen plan.", out error);
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private bool TryCreateCenterElements(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView view, ViewPlanBasicViewSpec spec, out string error)
        {
            error = null;
            try
            {
                foreach (ViewPlanCenterMarkSpec markSpec in spec.CenterMarks)
                {
                    List<CenterCircleMatch> matches;
                    if (!TryResolveCenterMarkEdges(view, markSpec, out matches, out error))
                        return false;
                    if (markSpec.Style == 2)
                    {
                        foreach (CenterCircleMatch match in matches)
                        {
                            drawingModel.ClearSelection2(true);
                            if (!view.SelectEntity(match.Edge, false))
                                return FailMessage("A uniquely resolved center-mark circle " +
                                    "could not be selected.", out error);
                            var mark = drawing.InsertCenterMark3(markSpec.Style,
                                markSpec.Propagate, markSpec.Slot) as ICenterMark;
                            if (mark == null || !TryApplyCenterMark(mark, markSpec, out error))
                                return false;
                        }
                    }
                    else
                    {
                        drawingModel.ClearSelection2(true);
                        for (int index = 0; index < matches.Count; index++)
                            if (!view.SelectEntity(matches[index].Edge, index > 0))
                                return FailMessage("A uniquely resolved center-mark group " +
                                    "circle could not be selected.", out error);
                        var mark = drawing.InsertCenterMark3(markSpec.Style,
                            markSpec.Propagate, markSpec.Slot) as ICenterMark;
                        if (mark == null || !TryApplyCenterMark(mark, markSpec, out error))
                            return false;
                    }
                }

                foreach (ViewPlanSymmetryCenterlineSpec lineSpec in
                    spec.SymmetryCenterlines)
                {
                    CenterLinePair pair;
                    if (!TryResolveSymmetryCenterlineEdges(view, lineSpec, out pair,
                        out error)) return false;
                    drawingModel.ClearSelection2(true);
                    if (!view.SelectEntity(pair.First.Edge, false) ||
                        !view.SelectEntity(pair.Second.Edge, true))
                        return FailMessage("The unique opposed edge pair could not be selected.",
                            out error);
                    var line = drawing.InsertCenterLine2() as ICenterLine;
                    if (line == null || line.GetAnnotation() == null)
                        return FailMessage("InsertCenterLine2 did not return a centerline " +
                            "annotation.", out error);
                    line.GetAnnotation().Color = lineSpec.Color;
                }
                drawingModel.ClearSelection2(true);
                drawingModel.ForceRebuild3(false);
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
            finally
            {
                drawingModel.ClearSelection2(true);
            }
        }

        private static bool TryApplyCenterMark(ICenterMark mark,
            ViewPlanCenterMarkSpec spec, out string error)
        {
            error = null;
            if (mark == null || mark.GetAnnotation() == null)
                return FailMessage("InsertCenterMark3 did not return a center-mark annotation.",
                    out error);
            mark.UseDocDisplaySettings = spec.UseDocumentDefaults;
            if (!spec.UseDocumentDefaults) mark.ShowLines = spec.ShowLines;
            mark.GetAnnotation().Color = spec.Color;
            return true;
        }

        private bool TryResolveCenterMarkEdges(IView view, ViewPlanCenterMarkSpec spec,
            out List<CenterCircleMatch> matches, out string error)
        {
            matches = new List<CenterCircleMatch>();
            error = null;
            if (spec.CircularEdges == null || spec.CircularEdges.Count == 0)
                return FailMessage("Center-mark feature circles were not resolved before COM.",
                    out error);
            IMathTransform transform = view.ModelToViewTransform;
            if (transform == null)
                return FailMessage("Center-mark view has no ModelToViewTransform.", out error);
            foreach (IEdge edge in VisibleEdges(view))
            {
                ICurve curve = edge.GetCurve() as ICurve;
                double[] circle = curve != null && curve.IsCircle()
                    ? curve.CircleParams as double[] : null;
                if (!FiniteCircle(circle)) continue;
                ViewPlanCircularEdgeSpec frozen = spec.CircularEdges.FirstOrDefault(item =>
                    CircleMatches(circle, item));
                if (frozen == null) continue;
                double[] projected;
                if (!TryTransformModelPointToView(transform, circle.Take(3).ToArray(),
                    out projected)) continue;
                if (matches.Any(item => Distance2(item.CenterSheet, projected) <=
                    PositionTolerance)) continue;
                matches.Add(new CenterCircleMatch
                {
                    Edge = edge,
                    CenterSheet = projected.Take(2).ToArray(),
                    FrozenEdgeId = frozen.EdgeId
                });
            }
            matches = matches.OrderBy(item => item.CenterSheet[0])
                .ThenBy(item => item.CenterSheet[1]).ToList();
            if (matches.Count != spec.ExpectedCount)
                return FailMessage("Frozen center-mark features resolved " + matches.Count +
                    " unique visible projected circles; expected_count is " +
                    spec.ExpectedCount + ".", out error);
            return true;
        }

        private bool TryResolveSymmetryCenterlineEdges(IView view,
            ViewPlanSymmetryCenterlineSpec spec, out CenterLinePair resolved,
            out string error)
        {
            resolved = null;
            error = null;
            double[] outline = view.GetOutline() as double[];
            if (outline == null || outline.Length < 4)
                return FailMessage("Centerline view outline is unavailable.", out error);
            double axisSpan = spec.Axis == "horizontal" ? outline[2] - outline[0] :
                outline[3] - outline[1];
            double perpendicularSpan = spec.Axis == "horizontal" ? outline[3] - outline[1] :
                outline[2] - outline[0];
            double perpendicularCenter = spec.Axis == "horizontal" ?
                (outline[1] + outline[3]) * 0.5 : (outline[0] + outline[2]) * 0.5;
            if (axisSpan <= PositionTolerance || perpendicularSpan <= PositionTolerance)
                return FailMessage("Centerline view outline has no positive span.", out error);
            var edges = new List<CenterLinearEdge>();
            IMathTransform transform = view.ModelToViewTransform;
            foreach (IEdge edge in VisibleEdges(view))
            {
                ICurve curve = edge.GetCurve() as ICurve;
                IVertex firstVertex = edge.GetStartVertex() as IVertex;
                IVertex secondVertex = edge.GetEndVertex() as IVertex;
                if (curve == null || !curve.IsLine() || firstVertex == null ||
                    secondVertex == null) continue;
                double[] first;
                double[] second;
                if (!TryTransformModelPointToView(transform,
                        firstVertex.GetPoint() as double[], out first) ||
                    !TryTransformModelPointToView(transform,
                        secondVertex.GetPoint() as double[], out second)) continue;
                double dx = second[0] - first[0];
                double dy = second[1] - first[1];
                double length = Math.Sqrt(dx * dx + dy * dy);
                double drift = spec.Axis == "horizontal" ? Math.Abs(dy) : Math.Abs(dx);
                if (length + PositionTolerance < spec.MinimumEdgeSpanRatio * axisSpan ||
                    drift > Math.Max(PositionTolerance, length * 1e-6)) continue;
                JArray segment = NormalizedSegment(first, second);
                if (edges.Any(item => JToken.DeepEquals(item.Segment, segment))) continue;
                edges.Add(new CenterLinearEdge
                {
                    Edge = edge,
                    First = first.Take(2).ToArray(),
                    Second = second.Take(2).ToArray(),
                    Segment = segment,
                    Perpendicular = spec.Axis == "horizontal" ?
                        (first[1] + second[1]) * 0.5 : (first[0] + second[0]) * 0.5,
                    AlongMin = spec.Axis == "horizontal" ?
                        Math.Min(first[0], second[0]) : Math.Min(first[1], second[1]),
                    AlongMax = spec.Axis == "horizontal" ?
                        Math.Max(first[0], second[0]) : Math.Max(first[1], second[1])
                });
            }
            var pairs = new List<CenterLinePair>();
            double symmetryTolerance = Math.Max(PositionTolerance,
                perpendicularSpan * 1e-6);
            double requiredOverlap = spec.MinimumEdgeSpanRatio * axisSpan;
            for (int first = 0; first < edges.Count; first++)
            for (int second = first + 1; second < edges.Count; second++)
            {
                CenterLinearEdge a = edges[first];
                CenterLinearEdge b = edges[second];
                if ((a.Perpendicular - perpendicularCenter) *
                        (b.Perpendicular - perpendicularCenter) >= 0.0 ||
                    Math.Abs((a.Perpendicular + b.Perpendicular) * 0.5 -
                        perpendicularCenter) > symmetryTolerance ||
                    Math.Min(a.AlongMax, b.AlongMax) - Math.Max(a.AlongMin, b.AlongMin) +
                        PositionTolerance < requiredOverlap) continue;
                pairs.Add(new CenterLinePair
                {
                    First = a,
                    Second = b,
                    PerpendicularSeparation = Math.Abs(a.Perpendicular - b.Perpendicular)
                });
            }
            if (pairs.Count == 0)
                return FailMessage("Symmetry centerline '" + spec.Id + "' resolved " +
                    "no opposed visible linear-edge pairs.",
                    out error);
            // Interior symmetric features can produce more than one qualifying pair.  The
            // outermost pair is the stable drawing datum for this selection strategy; keep
            // failing closed if more than one pair has the same maximum separation.
            double maximumSeparation = pairs.Max(item => item.PerpendicularSeparation);
            double separationTolerance = Math.Max(PositionTolerance,
                perpendicularSpan * 1e-6);
            var outermost = pairs.Where(item =>
                    Math.Abs(item.PerpendicularSeparation - maximumSeparation) <=
                        separationTolerance)
                .ToList();
            if (outermost.Count != 1)
                return FailMessage("Symmetry centerline '" + spec.Id + "' resolved " +
                    outermost.Count + " equally outermost opposed visible linear-edge pairs " +
                    "from " + pairs.Count + " candidates; exactly one is required.", out error);
            resolved = outermost[0];
            return true;
        }

        private static IEnumerable<IEdge> VisibleEdges(IView view)
        {
            var result = new List<IEdge>();
            var components = view.GetVisibleComponents() as Array;
            if (components == null) return result;
            foreach (object componentObject in components)
            {
                var component = componentObject as Component2;
                var entities = component != null ? view.GetVisibleEntities2(component, 1) as
                    Array : null;
                if (entities == null) continue;
                foreach (object entity in entities)
                {
                    var edge = entity as IEdge;
                    if (edge != null) result.Add(edge);
                }
            }
            return result;
        }

        private static bool FiniteCircle(double[] circle)
        {
            return circle != null && circle.Length >= 7 && circle[6] > 0.0 &&
                circle.Take(7).All(item => !double.IsNaN(item) &&
                    !double.IsInfinity(item));
        }

        private static bool CircleMatches(double[] live, ViewPlanCircularEdgeSpec frozen)
        {
            double tolerance = Math.Max(1e-8, frozen.RadiusModel * 1e-6);
            if (Math.Abs(live[6] - frozen.RadiusModel) > tolerance ||
                Distance3(live, frozen.CenterModel) > tolerance) return false;
            double axisLength = Math.Sqrt(live[3] * live[3] + live[4] * live[4] +
                live[5] * live[5]);
            if (axisLength <= 1e-12) return false;
            double dot = (live[3] * frozen.AxisModel[0] +
                live[4] * frozen.AxisModel[1] + live[5] * frozen.AxisModel[2]) /
                axisLength;
            return Math.Abs(Math.Abs(dot) - 1.0) <= 1e-6;
        }

        private static double Distance3(double[] first, double[] second)
        {
            double x = first[0] - second[0];
            double y = first[1] - second[1];
            double z = first[2] - second[2];
            return Math.Sqrt(x * x + y * y + z * z);
        }

        private bool TryResolveAuxiliaryReferenceEdge(IView parent,
            ViewPlanBasicViewSpec spec, out IEdge matchedEdge, out double[] expectedStart,
            out double[] expectedEnd, out double[] actualStart, out double[] actualEnd,
            out string error)
        {
            matchedEdge = null;
            expectedStart = null;
            expectedEnd = null;
            actualStart = null;
            actualEnd = null;
            error = null;
            IMathTransform transform = parent.ModelToViewTransform;
            if (transform == null)
                return FailMessage("Auxiliary parent has no ModelToViewTransform.", out error);
            if (!TryTransformModelPointToView(transform,
                    spec.AuxiliaryReferenceEdgeStartModel, out expectedStart) ||
                !TryTransformModelPointToView(transform,
                    spec.AuxiliaryReferenceEdgeEndModel, out expectedEnd))
                return FailMessage("Frozen auxiliary reference-edge endpoints could not be " +
                    "projected into the parent view.", out error);

            var matches = new List<AuxiliaryEdgeMatch>();
            var components = parent.GetVisibleComponents() as Array;
            if (components != null)
            {
                foreach (object componentObject in components)
                {
                    var component = componentObject as Component2;
                    if (component == null) continue;
                    var entities = parent.GetVisibleEntities2(component, 1) as Array;
                    if (entities == null) continue;
                    foreach (object entity in entities)
                    {
                        var edge = entity as IEdge;
                        var curve = edge != null ? edge.GetCurve() as ICurve : null;
                        var startVertex = edge != null ? edge.GetStartVertex() as IVertex : null;
                        var endVertex = edge != null ? edge.GetEndVertex() as IVertex : null;
                        if (curve == null || !curve.IsLine() || startVertex == null ||
                            endVertex == null) continue;
                        double[] start;
                        double[] end;
                        if (!TryTransformModelPointToView(transform,
                                startVertex.GetPoint() as double[], out start) ||
                            !TryTransformModelPointToView(transform,
                                endVertex.GetPoint() as double[], out end)) continue;
                        bool forward = Distance2(start, expectedStart) <=
                            spec.AuxiliaryMatchToleranceSheet &&
                            Distance2(end, expectedEnd) <=
                            spec.AuxiliaryMatchToleranceSheet;
                        bool reverse = Distance2(start, expectedEnd) <=
                            spec.AuxiliaryMatchToleranceSheet &&
                            Distance2(end, expectedStart) <=
                            spec.AuxiliaryMatchToleranceSheet;
                        if (forward || reverse)
                            matches.Add(new AuxiliaryEdgeMatch
                            {
                                Edge = edge,
                                Start = start,
                                End = end
                            });
                    }
                }
            }
            if (matches.Count != 1)
                return FailMessage("Frozen auxiliary reference edge matched " +
                    matches.Count.ToString(CultureInfo.InvariantCulture) +
                    " visible native linear edges; exactly one is required.", out error);
            matchedEdge = matches[0].Edge;
            actualStart = matches[0].Start;
            actualEnd = matches[0].End;
            return true;
        }

        private bool TryTransformModelPointToView(IMathTransform transform, double[] model,
            out double[] result)
        {
            result = null;
            if (transform == null || model == null || model.Length < 3) return false;
            try
            {
                var point = _mathUtility.CreatePoint(model) as IMathPoint;
                var transformed = point != null
                    ? point.MultiplyTransform(transform) as IMathPoint : null;
                result = transformed != null ? transformed.ArrayData as double[] : null;
                return result != null && result.Length >= 2 && result.All(item =>
                    !double.IsNaN(item) && !double.IsInfinity(item));
            }
            catch { return false; }
        }

        private static double Distance2(double[] first, double[] second)
        {
            double x = first[0] - second[0];
            double y = first[1] - second[1];
            return Math.Sqrt(x * x + y * y);
        }

        private bool TryCreateCircularProfile(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView sourceView, double offsetX, double offsetY, double radiusSheet,
            out ISketchSegment profile, out string error)
        {
            profile = null;
            error = null;
            if (!drawing.ActivateView(sourceView.Name))
                return FailMessage("The unique circular-profile source view could not be activated.",
                    out error);
            double[] position = sourceView.Position as double[];
            if (position == null || position.Length < 2)
                return FailMessage("Source-view position is unavailable for profile placement.",
                    out error);
            double[] centerModel;
            double modelPerSheet;
            if (!TrySheetPointToModel(sourceView, position[0] + offsetX,
                position[1] + offsetY, out centerModel, out modelPerSheet, out error))
                return false;
            double radiusModel = radiusSheet * modelPerSheet;
            if (radiusModel <= 0.0 || double.IsNaN(radiusModel) ||
                double.IsInfinity(radiusModel))
                return FailMessage("Circular profile radius did not transform to a positive " +
                    "finite model-space value.", out error);

            ISketchManager manager = drawingModel.SketchManager;
            bool previousAddToDb = manager.AddToDB;
            bool previousDisplay = manager.DisplayWhenAdded;
            try
            {
                drawingModel.ClearSelection2(true);
                // AddToDB prevents inference and grid snapping from moving the frozen center.
                manager.AddToDB = true;
                manager.DisplayWhenAdded = false;
                profile = manager.CreateCircleByRadius(centerModel[0], centerModel[1],
                    centerModel[2], radiusModel) as ISketchSegment;
            }
            finally
            {
                manager.DisplayWhenAdded = previousDisplay;
                manager.AddToDB = previousAddToDb;
            }
            if (profile == null)
                return FailMessage("SolidWorks did not create the circular profile.", out error);
            drawingModel.ClearSelection2(true);
            if (!profile.Select4(false, null))
                return FailMessage("The unique circular profile could not be selected.", out error);
            return true;
        }

        private bool TrySheetPointToModel(IView view, double sheetX, double sheetY,
            out double[] modelPoint, out double modelPerSheet, out string error)
        {
            modelPoint = null;
            modelPerSheet = 0.0;
            error = null;
            try
            {
                IMathTransform modelToView = view.ModelToViewTransform;
                double[] values = modelToView != null
                    ? modelToView.ArrayData as double[] : null;
                if (modelToView == null || values == null || values.Length < 13 ||
                    values[12] <= 0.0 || double.IsNaN(values[12]) ||
                    double.IsInfinity(values[12]))
                    return FailMessage("Source ModelToViewTransform is invalid.", out error);
                IMathTransform inverse = modelToView.IInverse();
                IMathPoint sheetPoint = _mathUtility.CreatePoint(
                    new[] { sheetX, sheetY, 0.0 }) as IMathPoint;
                IMathPoint transformed = sheetPoint != null && inverse != null
                    ? sheetPoint.MultiplyTransform(inverse) as IMathPoint : null;
                modelPoint = transformed != null ? transformed.ArrayData as double[] : null;
                if (modelPoint == null || modelPoint.Length < 3 ||
                    modelPoint.Take(3).Any(item => double.IsNaN(item) ||
                        double.IsInfinity(item)))
                    return FailMessage("Sheet-to-model profile transform failed.", out error);
                modelPerSheet = 1.0 / values[12];
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static bool TryResolveSectionPoints(IView parent, ViewPlanBasicViewSpec spec,
            out IList<double[]> points, out string error)
        {
            points = null;
            error = null;
            if (spec.Type != "full_section")
            {
                points = spec.SectionPointsModel;
                if (points == null || points.Count < 2)
                    return FailMessage("Explicit section points are unavailable.", out error);
                return true;
            }
            if (spec.SectionFeatureAxisOriginsModel == null ||
                spec.SectionFeatureAxisOriginsModel.Count == 0)
                return FailMessage("Full-section feature axes were not resolved before COM.",
                    out error);
            var transform = parent.ModelToViewTransform;
            double[] values = transform != null ? transform.ArrayData as double[] : null;
            if (values == null || values.Length < 13)
                return FailMessage("Parent ModelToViewTransform is unavailable.", out error);
            double[] axis = spec.SectionCuttingLineAxis == "horizontal"
                ? new[] { values[0], values[1], values[2] }
                : new[] { values[3], values[4], values[5] };
            Normalize(axis);
            double[] perpendicular = spec.SectionCuttingLineAxis == "horizontal"
                ? new[] { values[3], values[4], values[5] }
                : new[] { values[0], values[1], values[2] };
            Normalize(perpendicular);
            double[] anchor = new double[3];
            foreach (double[] origin in spec.SectionFeatureAxisOriginsModel)
                for (int index = 0; index < 3; index++) anchor[index] += origin[index];
            for (int index = 0; index < 3; index++)
                anchor[index] /= spec.SectionFeatureAxisOriginsModel.Count;
            double outlineSpan = 0.0;
            double[] outline = parent.GetOutline() as double[];
            if (outline != null && outline.Length >= 4)
                outlineSpan = spec.SectionCuttingLineAxis == "horizontal"
                    ? outline[2] - outline[0] : outline[3] - outline[1];
            if (outlineSpan <= 1e-9 || parent.ScaleDecimal <= 1e-12)
                return FailMessage("Parent outline cannot size the full-section cutting line.",
                    out error);
            double modelSpan = outlineSpan / parent.ScaleDecimal;
            double perpendicularTolerance = Math.Max(modelSpan * 1e-6, 1e-9);
            double reference = Dot(spec.SectionFeatureAxisOriginsModel[0], perpendicular);
            foreach (double[] origin in spec.SectionFeatureAxisOriginsModel)
                if (Math.Abs(Dot(origin, perpendicular) - reference) > perpendicularTolerance)
                    return FailMessage("Full-section feature axes do not share the requested " +
                        spec.SectionCuttingLineAxis + " cutting line in the parent view.", out error);
            if (spec.SectionFeatureAxisDirectionsModel == null ||
                spec.SectionFeatureAxisDirectionsModel.Count !=
                    spec.SectionFeatureAxisOriginsModel.Count)
                return FailMessage("Full-section feature-axis directions are unavailable.",
                    out error);
            foreach (double[] direction in spec.SectionFeatureAxisDirectionsModel)
                if (Math.Abs(Dot(direction, perpendicular)) > 1e-6)
                    return FailMessage("A full-section feature axis is outside the requested " +
                        "cutting plane.", out error);
            double halfLength = modelSpan * (0.5 + spec.SectionLineExtensionRatio.Value);
            points = new List<double[]>
            {
                new[] { anchor[0] - axis[0] * halfLength,
                    anchor[1] - axis[1] * halfLength,
                    anchor[2] - axis[2] * halfLength },
                new[] { anchor[0] + axis[0] * halfLength,
                    anchor[1] + axis[1] * halfLength,
                    anchor[2] + axis[2] * halfLength }
            };
            return true;
        }

        private static int SectionOptions(ViewPlanBasicViewSpec spec)
        {
            int options = 0;
            if (spec.Alignment == "not_aligned") options |= 1;
            if (spec.Type == "aligned_section") options |= 2;
            if (spec.SectionReverseDirection) options |= 4;
            if (spec.Type == "half_section") options |= 16;
            return options;
        }

        private static bool TryCreateExplicitModelView(IDrawingDoc drawing, IModelDoc2 sourceModel,
            ViewPlanBasicViewSpec spec, string temporaryName, out IView view, out string error)
        {
            view = null;
            error = null;
            bool dirtyBefore = sourceModel.GetSaveFlag();
            double[] original = null;
            bool temporaryCreated = false;
            string operationError = null;
            string cleanupError = null;
            try
            {
                IModelView active = sourceModel.IActiveView;
                if (active == null || active.Orientation3 == null)
                {
                    operationError = "The source model has no active orientation transform.";
                }
                else
                {
                    MathTransform transform = active.Orientation3;
                    original = transform.ArrayData as double[];
                    if (original == null || original.Length != 16)
                    {
                        operationError = "Orientation3 returned an unexpected transform array.";
                    }
                    else
                    {
                        double[] explicitTransform = BuildExplicitOrientationTransform(spec);
                        transform.ArrayData = explicitTransform;
                        active.Orientation3 = transform;
                        double[] applied = sourceModel.IActiveView.Orientation3.ArrayData as double[];
                        if (!ArraysEqual(applied, explicitTransform, 1e-10))
                        {
                            operationError = "SolidWorks did not accept the explicit orientation basis.";
                        }
                        else
                        {
                            sourceModel.NameView(temporaryName);
                            temporaryCreated = ModelViewNameExists(sourceModel, temporaryName);
                            if (!temporaryCreated)
                            {
                                operationError = "SolidWorks did not create the temporary named view.";
                            }
                            else
                            {
                                view = drawing.CreateDrawViewFromModelView3(sourceModel.GetPathName(),
                                    temporaryName, spec.X, spec.Y, 0.0) as IView;
                                if (view == null)
                                    operationError = "CreateDrawViewFromModelView3 returned null for " +
                                        "the explicit temporary orientation.";
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                operationError = ex.Message;
            }
            finally
            {
                try
                {
                    if (temporaryCreated && !sourceModel.DeleteNamedView(temporaryName))
                        cleanupError = "SolidWorks refused to delete the temporary named view.";
                }
                catch (Exception ex)
                {
                    cleanupError = "Temporary named-view deletion failed: " + ex.Message;
                }
                try
                {
                    if (original != null)
                    {
                        MathTransform restore = sourceModel.IActiveView.Orientation3;
                        restore.ArrayData = original;
                        sourceModel.IActiveView.Orientation3 = restore;
                    }
                }
                catch (Exception ex)
                {
                    cleanupError = AppendError(cleanupError,
                        "Source orientation restoration failed: " + ex.Message);
                }
                if (ModelViewNameExists(sourceModel, temporaryName))
                    cleanupError = AppendError(cleanupError,
                        "Temporary named view still exists after cleanup.");
                if (original != null)
                {
                    double[] restored = sourceModel.IActiveView.Orientation3.ArrayData as double[];
                    if (!ArraysEqual(restored, original, 1e-10))
                        cleanupError = AppendError(cleanupError,
                            "Source orientation differs after cleanup.");
                }
                if (sourceModel.GetSaveFlag() != dirtyBefore)
                    cleanupError = AppendError(cleanupError,
                        "Source-model dirty state differs after temporary orientation cleanup.");
            }

            if (operationError != null || cleanupError != null)
            {
                error = AppendError(operationError, cleanupError);
                return false;
            }
            return view != null;
        }

        private static double[] BuildExplicitOrientationTransform(ViewPlanBasicViewSpec spec)
        {
            double[] right = Cross(spec.ViewDirectionModel, spec.UpDirectionModel);
            Normalize(right);
            return new[]
            {
                right[0], right[1], right[2],
                spec.UpDirectionModel[0], spec.UpDirectionModel[1], spec.UpDirectionModel[2],
                -spec.ViewDirectionModel[0], -spec.ViewDirectionModel[1],
                -spec.ViewDirectionModel[2],
                0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0
            };
        }

        private static string BuildTransientViewName(ViewPlanBasicExecutionPlan plan,
            ViewPlanBasicViewSpec spec)
        {
            string hash = plan.PlanCanonicalSha256 ?? "";
            if (hash.Length > 12) hash = hash.Substring(0, 12);
            return "__Q3DS_VP_TMP_" + hash + "_" +
                spec.OriginalIndex.ToString(CultureInfo.InvariantCulture);
        }

        private static bool TryApplyContract(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IView view, ViewPlanBasicViewSpec spec, ViewPlanBasicExecutionPlan plan,
            out string error)
        {
            error = null;
            try
            {
                view.PositionLocked = false;
                view.Position = new[] { spec.X, spec.Y };
                if (spec.Type == "projected_view")
                {
                    view.LinkParentConfiguration = true;
                    view.UseParentScale = true;
                }
                else if (IsSectionType(spec.Type))
                {
                    view.LinkParentConfiguration = true;
                    view.UseParentScale = false;
                    view.UseSheetScale = 0;
                    view.ScaleDecimal = spec.Scale;
                }
                else if (spec.Type == "detail_view")
                {
                    view.LinkParentConfiguration = true;
                    view.UseParentScale = false;
                    view.UseSheetScale = 0;
                    view.ScaleDecimal = spec.Scale;
                }
                else if (spec.Type == "auxiliary_view")
                {
                    view.LinkParentConfiguration = true;
                    view.UseParentScale = false;
                    view.UseSheetScale = 0;
                    view.ScaleDecimal = spec.Scale;
                }
                else
                {
                    view.LinkParentConfiguration = false;
                    view.ReferencedConfiguration = plan.Configuration;
                    view.UseParentScale = false;
                    view.UseSheetScale = 0;
                    view.ScaleDecimal = spec.Scale;
                }
                if (!string.IsNullOrEmpty(plan.DisplayState))
                    view.DisplayState = plan.DisplayState;

                int displayMode = DisplayModeValue(spec);
                int writeMode = spec.DisplayMode == "shaded_with_edges" ? 3 : displayMode;
                if (!view.SetDisplayMode3(false, writeMode, false, spec.Edges))
                {
                    error = "SetDisplayMode3 rejected the frozen display contract.";
                    return false;
                }
                view.SetDisplayTangentEdges2(TangentEdgeValue(spec.TangentEdges));

                if (IsModelOrientationType(spec.Type) &&
                    Math.Abs(spec.RollAngleRad) > 1e-12)
                {
                    drawingModel.ClearSelection2(true);
                    bool selected = drawingModel.Extension.SelectByID2(view.Name, "DRAWINGVIEW",
                        spec.X, spec.Y, 0.0, false, 0, null, 0);
                    if (!selected || !drawing.DrawingViewRotate(spec.RollAngleRad))
                    {
                        error = "DrawingViewRotate rejected roll_angle_rad.";
                        return false;
                    }
                    drawingModel.ClearSelection2(true);
                    view.Position = new[] { spec.X, spec.Y };
                }

                view.PositionLocked = true;
                drawingModel.ForceRebuild3(false);
                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
        }

        private bool TryReadAndVerify(IModelDoc2 drawingModel, IView view,
            ViewPlanBasicViewSpec spec,
            ViewPlanBasicExecutionPlan plan, Dictionary<string, IView> created,
            out JObject row, out string error)
        {
            error = null;
            double[] position = view.Position as double[];
            var baseView = view.GetBaseView() as IView;
            row = new JObject
            {
                ["id"] = spec.Id,
                ["type"] = spec.Type,
                ["name"] = view.Name,
                ["unique_name"] = SafeUniqueName(view),
                ["solidworks_view_type"] = view.Type,
                ["position_sheet_m"] = position == null ? null : new JArray(position),
                ["scale"] = view.ScaleDecimal,
                ["angle_rad"] = view.Angle,
                ["configuration"] = view.ReferencedConfiguration,
                ["display_state"] = view.DisplayState,
                ["display_mode"] = view.GetDisplayMode2(),
                ["edges_in_shaded_mode"] = view.GetDisplayEdgesInShadedMode(),
                ["tangent_edges"] = view.GetDisplayTangentEdges2(),
                ["position_locked"] = view.PositionLocked,
                ["orientation_name"] = view.GetOrientationName(),
                ["referenced_model"] = view.GetReferencedModelName()
            };
            if (baseView != null)
            {
                row["parent_name"] = baseView.Name;
                row["parent_unique_name"] = SafeUniqueName(baseView);
            }

            if (IsSectionType(spec.Type))
            {
                JObject section;
                if (!TryReadSectionContract(view, spec, out section, out error))
                {
                    row["section"] = section;
                    return false;
                }
                row["section"] = section;
            }
            else if (spec.Type == "broken_out_section")
            {
                JObject brokenOut;
                if (!TryReadBrokenOutContract(drawingModel, view, spec,
                    out brokenOut, out error))
                {
                    row["c2"] = brokenOut;
                    return false;
                }
                row["c2"] = brokenOut;
            }
            else if (spec.Type == "detail_view")
            {
                JObject detail;
                if (!TryReadDetailContract(view, spec, out detail, out error))
                {
                    row["c2"] = detail;
                    return false;
                }
                row["c2"] = detail;
            }
            else if (spec.Type == "auxiliary_view")
            {
                JObject auxiliary;
                if (!TryReadAuxiliaryContract(view, spec, out auxiliary, out error))
                {
                    row["auxiliary"] = auxiliary;
                    return false;
                }
                row["auxiliary"] = auxiliary;
            }

            JObject centerElements;
            if (!TryReadCenterElements(view, spec, out centerElements, out error))
            {
                row["center_elements"] = centerElements;
                return false;
            }
            row["center_elements"] = centerElements;

            if (!UsesNativeGeneratedName(spec.Type) &&
                !string.Equals(view.Name, ViewNamePrefix + spec.Id, StringComparison.Ordinal))
                error = "Deterministic name readback differs from the plan.";
            else if (UsesNativeGeneratedName(spec.Type) &&
                string.IsNullOrWhiteSpace(SafeUniqueName(view)))
                error = "Native-named view persistent unique handle is unavailable.";
            else if (position == null || position.Length < 2 ||
                Math.Abs(position[0] - spec.X) > PositionTolerance ||
                Math.Abs(position[1] - spec.Y) > PositionTolerance)
                error = "Position readback differs from position_sheet_m.";
            else if (Math.Abs(view.ScaleDecimal - spec.Scale) > ScaleTolerance)
                error = "Scale readback differs from the frozen scale.";
            else if (IsModelOrientationType(spec.Type) &&
                AngularDistance(view.Angle, spec.RollAngleRad) > 1e-9)
                error = "Roll-angle readback differs from roll_angle_rad.";
            else if (!string.Equals(view.ReferencedConfiguration, plan.Configuration,
                    StringComparison.OrdinalIgnoreCase))
                error = "Referenced configuration readback differs from the plan.";
            else if (!PathEquals(view.GetReferencedModelName(), plan.ModelPath))
                error = "Referenced model path readback differs from model_path.";
            else if (!string.IsNullOrEmpty(plan.DisplayState) &&
                !string.Equals(view.DisplayState, plan.DisplayState,
                    StringComparison.OrdinalIgnoreCase))
                error = "Display-state readback differs from the plan.";
            else if (view.GetDisplayMode2() != ReadbackDisplayModeValue(spec))
                error = "Display-mode readback differs from the plan.";
            else if ((spec.DisplayMode == "shaded_with_edges") !=
                view.GetDisplayEdgesInShadedMode())
                error = "Shaded-edge readback differs from the plan.";
            else if (view.GetDisplayTangentEdges2() != TangentEdgeValue(spec.TangentEdges))
                error = "Tangent-edge readback differs from the plan.";
            else if (!view.PositionLocked)
                error = "Position lock was not retained in memory.";
            else if (spec.OrientationKind == "explicit_basis" &&
                !ExplicitRotationMatches(view, spec))
                error = "Explicit-basis rotation readback differs from the requested basis.";
            else if (spec.Type == "projected_view" || IsSectionType(spec.Type) ||
                spec.Type == "detail_view" || spec.Type == "auxiliary_view")
            {
                if (!created.TryGetValue(spec.ParentId, out IView parent) || baseView == null ||
                    !string.Equals(SafeUniqueName(parent), SafeUniqueName(baseView),
                        StringComparison.Ordinal))
                    error = "Parent-derived view did not retain the required unique parent.";
                else if (spec.Type == "projected_view" && view.Type != 4)
                    error = "Projected view has unexpected SolidWorks type " + view.Type + ".";
                else if (IsSectionType(spec.Type) && view.Type != 2)
                    error = "Section view has unexpected SolidWorks type " + view.Type + ".";
                else if (spec.Type == "detail_view" && view.Type != 3)
                    error = "Detail view has unexpected SolidWorks type " + view.Type + ".";
                else if (spec.Type == "auxiliary_view" && view.Type != 5)
                    error = "Auxiliary view has unexpected SolidWorks type " + view.Type + ".";
            }
            else if (spec.Type == "broken_out_section" && view.Type != 7)
                error = "Broken-out base view has unexpected SolidWorks type " + view.Type + ".";
            return error == null;
        }

        private bool TryReadBrokenOutContract(IModelDoc2 drawingModel, IView view,
            ViewPlanBasicViewSpec spec, out JObject contract, out string error)
        {
            contract = new JObject();
            error = null;
            if (drawingModel == null)
                return FailMessage("Drawing model is unavailable for broken-out readback.",
                    out error);
            try
            {
                int count = view.GetBreakOutSectionCount();
                var features = view.GetBreakOutSections() as Array;
                if (count != 1 || features == null || features.Length != 1)
                    return FailMessage("Broken-out readback must contain exactly one feature.",
                        out error);
                var feature = features.GetValue(0) as IFeature;
                if (feature == null ||
                    !string.Equals(feature.GetTypeName2(), "DrBreakoutSectionLine",
                        StringComparison.Ordinal))
                    return FailMessage("Broken-out feature type is unavailable or unexpected.",
                        out error);
                var data = feature.GetDefinition() as IBrokenOutSectionFeatureData;
                if (data == null)
                    return FailMessage("Broken-out feature data is unavailable.", out error);
                bool accessed = false;
                try
                {
                    accessed = data.AccessSelections(drawingModel, null);
                    if (!accessed)
                        return FailMessage("Broken-out feature selections cannot be accessed.",
                            out error);
                    double depth = data.Depth;
                    if (double.IsNaN(depth) || double.IsInfinity(depth) || depth <= 0.0 ||
                        Math.Abs(depth - spec.BrokenOutDepth) >
                            Math.Max(1e-9, spec.BrokenOutDepth * 1e-8))
                        return FailMessage("Broken-out depth differs from depth_m.", out error);
                    data.EditSketch = true;
                    int segmentCount = data.GetSketchSegmentCount();
                    var segments = data.SketchSegment as Array;
                    if (segmentCount != 1 || segments == null || segments.Length != 1)
                        return FailMessage("Broken-out boundary must contain exactly one segment.",
                            out error);
                    var segment = segments.GetValue(0) as ISketchSegment;
                    JObject profile;
                    if (!TryReadCircleProfile(view, segment, spec.ProfileOffsetX,
                        spec.ProfileOffsetY, spec.ProfileRadiusSheet, out profile, out error))
                        return false;
                    contract["kind"] = "broken_out_section";
                    contract["feature_type"] = feature.GetTypeName2();
                    contract["feature_count"] = count;
                    contract["depth_m"] = Quantize(depth);
                    contract["profile_item_count"] = segmentCount;
                    contract["profile"] = profile;
                    return true;
                }
                finally
                {
                    try { data.EditSketch = false; } catch { }
                    if (accessed) data.ReleaseSelectionAccess();
                }
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private bool TryReadDetailContract(IView view, ViewPlanBasicViewSpec spec,
            out JObject contract, out string error)
        {
            contract = new JObject();
            error = null;
            try
            {
                var data = view.GetDetail() as IDetailCircle;
                var parent = view.GetBaseView() as IView;
                if (data == null || parent == null)
                    return FailMessage("Detail readback is missing IDetailCircle or its parent.",
                        out error);
                if (!string.Equals(data.GetLabel(), spec.DetailLabel, StringComparison.Ordinal))
                    return FailMessage("Detail label differs from the frozen label.", out error);
                if (data.GetStyle() != spec.DetailStyle ||
                    data.GetDisplay() != spec.DetailShowType ||
                    data.HasFullOutline() != spec.DetailFullOutline ||
                    data.JaggedOutline != spec.DetailJaggedOutline ||
                    data.NoOutline != spec.DetailNoOutline ||
                    (spec.DetailJaggedOutline &&
                     data.ShapeIntensity != spec.DetailShapeIntensity))
                    return FailMessage("Detail style, display, outline, or intensity differs " +
                        "from detail_definition.", out error);
                IView linked = data.GetDetailView();
                if (linked == null || !string.Equals(SafeUniqueName(linked),
                    SafeUniqueName(view), StringComparison.Ordinal))
                    return FailMessage("Detail circle does not resolve to the planned detail view.",
                        out error);
                int profileCount = data.GetProfileItemsCount();
                var profileItems = data.GetProfileItems() as Array;
                if (profileCount != 1 || profileItems == null || profileItems.Length != 1)
                    return FailMessage("Detail profile must contain exactly one item.", out error);
                JObject profile;
                if (!TryReadCircleProfile(parent, profileItems.GetValue(0) as ISketchSegment,
                    spec.ProfileOffsetX, spec.ProfileOffsetY, spec.ProfileRadiusSheet,
                    out profile, out error)) return false;
                double labelX = 0.0;
                double labelY = 0.0;
                data.GetLabelPosition(out labelX, out labelY);
                if (double.IsNaN(labelX) || double.IsInfinity(labelX) ||
                    double.IsNaN(labelY) || double.IsInfinity(labelY))
                    return FailMessage("Detail label position is not finite.", out error);
                if (spec.DetailLabelPositionMode == "explicit" &&
                    (Math.Abs(labelX - spec.DetailLabelX.Value) > PositionTolerance ||
                     Math.Abs(labelY - spec.DetailLabelY.Value) > PositionTolerance))
                    return FailMessage(string.Format(CultureInfo.InvariantCulture,
                        "Detail label position differs from the explicit plan: " +
                        "actual=({0:R},{1:R}), expected=({2:R},{3:R}).",
                        labelX, labelY, spec.DetailLabelX.Value,
                        spec.DetailLabelY.Value), out error);

                contract["kind"] = "detail_view";
                contract["label"] = data.GetLabel();
                contract["style"] = data.GetStyle();
                contract["show_type"] = data.GetDisplay();
                contract["full_outline"] = data.HasFullOutline();
                contract["jagged_outline"] = data.JaggedOutline;
                contract["no_outline"] = data.NoOutline;
                contract["shape_intensity_mode"] = spec.DetailJaggedOutline
                    ? "effective" : "not_applicable";
                contract["shape_intensity_declared"] = spec.DetailShapeIntensity;
                contract["shape_intensity_actual"] = spec.DetailJaggedOutline
                    ? new JValue(data.ShapeIntensity) : JValue.CreateNull();
                contract["profile_item_count"] = profileCount;
                contract["profile"] = profile;
                contract["label_position_mode"] = spec.DetailLabelPositionMode;
                contract["label_position_sheet_m"] = new JArray(
                    Quantize(labelX), Quantize(labelY));
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private bool TryReadAuxiliaryContract(IView view, ViewPlanBasicViewSpec spec,
            out JObject contract, out string error)
        {
            contract = new JObject();
            error = null;
            try
            {
                var parent = view.GetBaseView() as IView;
                if (parent == null)
                    return FailMessage("Auxiliary readback is missing its parent view.",
                        out error);
                if (view.Type != 5)
                    return FailMessage("Auxiliary readback has unexpected SolidWorks type " +
                        view.Type.ToString(CultureInfo.InvariantCulture) + ".", out error);
                int alignment = view.GetAlignment();
                if ((spec.AuxiliaryNotAligned && alignment != 0) ||
                    (!spec.AuxiliaryNotAligned && alignment == 0))
                    return FailMessage("Auxiliary alignment state differs from " +
                        "auxiliary_definition.not_aligned.", out error);

                IEdge ignoredEdge;
                double[] expectedStart;
                double[] expectedEnd;
                double[] actualStart;
                double[] actualEnd;
                if (!TryResolveAuxiliaryReferenceEdge(parent, spec, out ignoredEdge,
                    out expectedStart, out expectedEnd, out actualStart, out actualEnd,
                    out error)) return false;
                bool actualFlip;
                double orientationSide;
                double[] auxiliaryRotation;
                if (!TryReadAuxiliaryFlip(parent, view, actualStart, actualEnd,
                    out actualFlip, out orientationSide, out auxiliaryRotation, out error))
                    return false;
                if (actualFlip != spec.AuxiliaryFlip)
                    return FailMessage("Auxiliary orientation differs from " +
                        "auxiliary_definition.flip.", out error);

                var arrow = view.GetProjectionArrow() as IProjectionArrow;
                if (arrow == null)
                    return FailMessage("Auxiliary view did not expose its projection arrow.",
                        out error);
                var projected = arrow.GetProjectedView() as IView;
                var source = arrow.GetView() as IView;
                if (projected == null || source == null ||
                    !string.Equals(SafeUniqueName(projected), SafeUniqueName(view),
                        StringComparison.Ordinal) ||
                    !string.Equals(SafeUniqueName(source), SafeUniqueName(parent),
                        StringComparison.Ordinal))
                    return FailMessage("Projection arrow does not uniquely link the auxiliary " +
                        "view to its frozen parent.", out error);
                if (arrow.Visible != spec.AuxiliaryShowArrow)
                    return FailMessage("Auxiliary projection-arrow visibility differs from " +
                        "auxiliary_definition.show_arrow.", out error);
                double[] coordinates = arrow.GetCoordinates() as double[];
                if (coordinates == null || coordinates.Length < 24 ||
                    coordinates.Any(item => double.IsNaN(item) || double.IsInfinity(item)))
                    return FailMessage("Auxiliary projection-arrow coordinates are unavailable " +
                        "or non-finite.", out error);

                double labelX;
                double labelY;
                JObject managedLabel = null;
                string labelRenderer;
                if (spec.AuxiliaryLabelPositionMode == "explicit")
                {
                    if (!string.IsNullOrEmpty(arrow.GetLabel()))
                        return FailMessage("Explicit auxiliary placement requires an empty " +
                            "non-positionable native arrow label.", out error);
                    if (!TryReadManagedAuxiliaryLabel(parent, arrow, spec,
                        out managedLabel, out error)) return false;
                    var managedPosition = managedLabel["position_sheet_m"] as JArray;
                    labelX = managedPosition[0].Value<double>();
                    labelY = managedPosition[1].Value<double>();
                    labelRenderer = "repository_managed_parent_view_note";
                }
                else
                {
                    if (!string.Equals(arrow.GetLabel(), spec.AuxiliaryLabel,
                        StringComparison.Ordinal))
                        return FailMessage("Auxiliary projection-arrow label differs from the " +
                            "frozen label.", out error);
                    string managedName = BuildManagedAuxiliaryLabelName(spec);
                    if (ManagedAuxiliaryLabelAnnotations(parent, managedName).Length != 0)
                        return FailMessage("Document-default auxiliary placement contains an " +
                            "unexpected repository-managed label.", out error);
                    labelX = coordinates[21];
                    labelY = coordinates[22];
                    labelRenderer = "native_projection_arrow";
                }

                JArray edge = NormalizedSegment(actualStart, actualEnd);
                contract["kind"] = "auxiliary_view";
                contract["reference_edge_projected_sheet_m"] = edge;
                contract["match_tolerance_sheet_m"] =
                    Quantize(spec.AuxiliaryMatchToleranceSheet);
                contract["not_aligned"] = spec.AuxiliaryNotAligned;
                contract["alignment_actual"] = alignment;
                contract["show_arrow"] = arrow.Visible;
                contract["flip"] = actualFlip;
                contract["flip_view_property"] = view.FlipView;
                contract["orientation_side"] = Quantize(orientationSide);
                contract["model_to_view_rotation"] = new JArray(
                    auxiliaryRotation.Select(Quantize));
                contract["label"] = spec.AuxiliaryLabel;
                contract["native_projection_arrow_label"] = arrow.GetLabel();
                contract["label_position_mode"] = spec.AuxiliaryLabelPositionMode;
                contract["label_renderer"] = labelRenderer;
                contract["arrow_line_sheet_m"] = new JArray(
                    Quantize(coordinates[0]), Quantize(coordinates[1]),
                    Quantize(coordinates[3]), Quantize(coordinates[4]));
                contract["native_label_position_sheet_m"] = new JArray(
                    Quantize(coordinates[21]), Quantize(coordinates[22]));
                contract["label_position_sheet_m"] = new JArray(
                    Quantize(labelX), Quantize(labelY));
                contract["managed_label"] = managedLabel == null
                    ? (JToken)JValue.CreateNull() : managedLabel;
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static bool TryReadAuxiliaryFlip(IView parent, IView auxiliary,
            double[] edgeStart, double[] edgeEnd, out bool flip, out double side,
            out double[] auxiliaryRotation, out string error)
        {
            flip = false;
            side = 0.0;
            auxiliaryRotation = null;
            error = null;
            try
            {
                double[] parentValues = parent.ModelToViewTransform.ArrayData as double[];
                double[] auxiliaryValues = auxiliary.ModelToViewTransform.ArrayData as double[];
                if (parentValues == null || parentValues.Length < 9 ||
                    auxiliaryValues == null || auxiliaryValues.Length < 9)
                    return FailMessage("Auxiliary orientation transforms are unavailable.",
                        out error);
                double dx = edgeEnd[0] - edgeStart[0];
                double dy = edgeEnd[1] - edgeStart[1];
                if (Math.Sqrt(dx * dx + dy * dy) <= 1e-12)
                    return FailMessage("Auxiliary reference edge has a zero projected length.",
                        out error);
                double[] right = { parentValues[0], parentValues[1], parentValues[2] };
                double[] up = { parentValues[3], parentValues[4], parentValues[5] };
                double[] parentDirection =
                    { -parentValues[6], -parentValues[7], -parentValues[8] };
                double[] edgeDirection =
                {
                    right[0] * dx + up[0] * dy,
                    right[1] * dx + up[1] * dy,
                    right[2] * dx + up[2] * dy
                };
                Normalize(parentDirection);
                Normalize(edgeDirection);
                double[] unflippedDirection = Cross(parentDirection, edgeDirection);
                Normalize(unflippedDirection);
                double[] auxiliaryDirection =
                    { -auxiliaryValues[6], -auxiliaryValues[7], -auxiliaryValues[8] };
                Normalize(auxiliaryDirection);
                side = Dot(auxiliaryDirection, unflippedDirection);
                if (Math.Abs(side) < 0.999999)
                    return FailMessage("Auxiliary orientation is not perpendicular to the " +
                        "frozen projected reference edge.", out error);
                flip = side < 0.0;
                auxiliaryRotation = auxiliaryValues.Take(9).ToArray();
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static JArray NormalizedSegment(double[] first, double[] second)
        {
            double firstX = Quantize(first[0]);
            double firstY = Quantize(first[1]);
            double secondX = Quantize(second[0]);
            double secondY = Quantize(second[1]);
            bool reverse = firstX > secondX ||
                (firstX == secondX && firstY > secondY);
            return reverse
                ? new JArray(new JArray(secondX, secondY), new JArray(firstX, firstY))
                : new JArray(new JArray(firstX, firstY), new JArray(secondX, secondY));
        }

        private bool TryReadCenterElements(IView view, ViewPlanBasicViewSpec spec,
            out JObject contract, out string error)
        {
            contract = new JObject
            {
                ["center_marks"] = new JArray(),
                ["symmetry_centerlines"] = new JArray()
            };
            error = null;
            try
            {
                List<ActualCenterMark> actualMarks;
                if (!TryEnumerateCenterMarks(view, out actualMarks, out error)) return false;
                var usedMarks = new HashSet<int>();
                var markRows = (JArray)contract["center_marks"];
                foreach (ViewPlanCenterMarkSpec planned in spec.CenterMarks)
                {
                    List<CenterCircleMatch> expectedEdges;
                    if (!TryResolveCenterMarkEdges(view, planned, out expectedEdges,
                        out error)) return false;
                    List<double[]> expectedCenters = expectedEdges
                        .Select(item => item.CenterSheet).ToList();
                    var selected = new List<ActualCenterMark>();
                    if (planned.Style == 2)
                    {
                        foreach (double[] center in expectedCenters)
                        {
                            List<ActualCenterMark> candidates = actualMarks.Where(item =>
                                !usedMarks.Contains(item.Index) && item.Style == 2 &&
                                item.Centers.Count == 1 &&
                                Distance2(item.Centers[0], center) <= PositionTolerance)
                                .ToList();
                            if (candidates.Count != 1)
                                return FailMessage("Center mark '" + planned.Id + "' matched " +
                                    candidates.Count + " native single marks at a frozen " +
                                    "projected center; exactly one is required.", out error);
                            selected.Add(candidates[0]);
                            usedMarks.Add(candidates[0].Index);
                        }
                    }
                    else
                    {
                        List<ActualCenterMark> candidates = actualMarks.Where(item =>
                            !usedMarks.Contains(item.Index) && item.Style == planned.Style &&
                            SameCenters(item.Centers, expectedCenters)).ToList();
                        if (candidates.Count != 1)
                            return FailMessage("Center-mark group '" + planned.Id + "' matched " +
                                candidates.Count + " native groups; exactly one is required.",
                                out error);
                        selected.Add(candidates[0]);
                        usedMarks.Add(candidates[0].Index);
                    }
                    foreach (ActualCenterMark actual in selected)
                    {
                        if (actual.UseDocumentDefaults != planned.UseDocumentDefaults ||
                            actual.ShowLines != planned.ShowLines ||
                            actual.Color != planned.Color)
                            return FailMessage("Center mark '" + planned.Id +
                                "' display-property readback differs from the frozen plan.",
                                out error);
                    }
                    markRows.Add(new JObject
                    {
                        ["id"] = planned.Id,
                        ["style"] = planned.StyleName,
                        ["style_value"] = planned.Style,
                        ["expected_count"] = planned.ExpectedCount,
                        ["native_object_count"] = selected.Count,
                        ["use_document_defaults"] = planned.UseDocumentDefaults,
                        ["show_lines"] = planned.ShowLines,
                        ["propagate"] = planned.Propagate,
                        ["slot"] = planned.Slot,
                        ["color"] = planned.Color,
                        ["projected_centers_sheet_m"] = CenterArray(expectedCenters)
                    });
                }
                if (usedMarks.Count != actualMarks.Count)
                    return FailMessage("The view contains " +
                        (actualMarks.Count - usedMarks.Count) +
                        " unplanned native center-mark objects.", out error);

                List<ActualCenterLine> actualLines;
                if (!TryEnumerateCenterLines(view, out actualLines, out error)) return false;
                var usedLines = new HashSet<int>();
                var lineRows = (JArray)contract["symmetry_centerlines"];
                foreach (ViewPlanSymmetryCenterlineSpec planned in
                    spec.SymmetryCenterlines)
                {
                    CenterLinePair expected;
                    if (!TryResolveSymmetryCenterlineEdges(view, planned, out expected,
                        out error)) return false;
                    var expectedSegments = new List<JArray>
                        { expected.First.Segment, expected.Second.Segment };
                    List<ActualCenterLine> candidates = actualLines.Where(item =>
                        !usedLines.Contains(item.Index) && item.Color == planned.Color &&
                        SameSegments(item.Segments, expectedSegments)).ToList();
                    if (candidates.Count != 1)
                        return FailMessage("Symmetry centerline '" + planned.Id + "' matched " +
                            candidates.Count + " native centerlines; exactly one is required.",
                            out error);
                    usedLines.Add(candidates[0].Index);
                    lineRows.Add(new JObject
                    {
                        ["id"] = planned.Id,
                        ["axis"] = planned.Axis,
                        ["minimum_edge_span_ratio"] = Quantize(
                            planned.MinimumEdgeSpanRatio),
                        ["color"] = planned.Color,
                        ["attached_edge_segments_sheet_m"] = SegmentArray(expectedSegments)
                    });
                }
                if (usedLines.Count != actualLines.Count)
                    return FailMessage("The view contains " +
                        (actualLines.Count - usedLines.Count) +
                        " unplanned native centerlines.", out error);
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private bool TryEnumerateCenterMarks(IView view, out List<ActualCenterMark> result,
            out string error)
        {
            result = new List<ActualCenterMark>();
            error = null;
            ICenterMark current = view.GetFirstCenterMark2() as ICenterMark;
            int guard = 0;
            while (current != null && guard++ < 2048)
            {
                IAnnotation annotation = current.GetAnnotation();
                if (annotation == null)
                    return FailMessage("A native center mark has no annotation.", out error);
                List<double[]> centers;
                if (!TryReadAttachedCircleCenters(view, annotation, out centers, out error))
                    return false;
                result.Add(new ActualCenterMark
                {
                    Index = result.Count,
                    Style = current.Style,
                    UseDocumentDefaults = current.UseDocDisplaySettings,
                    ShowLines = current.ShowLines,
                    Color = annotation.Color,
                    Centers = centers
                });
                current = current.GetNext() as ICenterMark;
            }
            if (current != null)
                return FailMessage("Center-mark enumeration exceeded its safety bound.",
                    out error);
            return true;
        }

        private bool TryEnumerateCenterLines(IView view, out List<ActualCenterLine> result,
            out string error)
        {
            result = new List<ActualCenterLine>();
            error = null;
            ICenterLine current = view.GetFirstCenterLine() as ICenterLine;
            int guard = 0;
            while (current != null && guard++ < 2048)
            {
                IAnnotation annotation = current.GetAnnotation();
                if (annotation == null)
                    return FailMessage("A native centerline has no annotation.", out error);
                List<JArray> segments;
                if (!TryReadAttachedLinearSegments(view, annotation, out segments, out error))
                    return false;
                if (segments.Count != 2)
                    return FailMessage("A native symmetry centerline must remain attached to " +
                        "exactly two linear edges.", out error);
                result.Add(new ActualCenterLine
                {
                    Index = result.Count,
                    Color = annotation.Color,
                    Segments = segments
                });
                current = current.GetNext() as ICenterLine;
            }
            if (current != null)
                return FailMessage("Centerline enumeration exceeded its safety bound.",
                    out error);
            return true;
        }

        private bool TryReadAttachedCircleCenters(IView view, IAnnotation annotation,
            out List<double[]> centers, out string error)
        {
            centers = new List<double[]>();
            error = null;
            Array entities = annotation.GetAttachedEntities3() as Array;
            if (entities == null)
                return FailMessage("Center-mark attached entities are unavailable.", out error);
            IMathTransform transform = view.ModelToViewTransform;
            foreach (object entity in entities)
            {
                IEdge edge = entity as IEdge;
                ICurve curve = edge != null ? edge.GetCurve() as ICurve : null;
                double[] circle = curve != null && curve.IsCircle()
                    ? curve.CircleParams as double[] : null;
                double[] projected;
                if (!FiniteCircle(circle) || !TryTransformModelPointToView(transform,
                    circle.Take(3).ToArray(), out projected))
                    return FailMessage("A center mark is not attached exclusively to finite " +
                        "circular edges.", out error);
                double[] center = projected.Take(2).ToArray();
                if (!centers.Any(item => Distance2(item, center) <= PositionTolerance))
                    centers.Add(center);
            }
            centers = centers.OrderBy(item => item[0]).ThenBy(item => item[1]).ToList();
            if (centers.Count == 0)
                return FailMessage("A center mark has no unique projected circle center.",
                    out error);
            return true;
        }

        private bool TryReadAttachedLinearSegments(IView view, IAnnotation annotation,
            out List<JArray> segments, out string error)
        {
            segments = new List<JArray>();
            error = null;
            Array entities = annotation.GetAttachedEntities3() as Array;
            if (entities == null)
                return FailMessage("Centerline attached entities are unavailable.", out error);
            IMathTransform transform = view.ModelToViewTransform;
            foreach (object entity in entities)
            {
                IEdge edge = entity as IEdge;
                ICurve curve = edge != null ? edge.GetCurve() as ICurve : null;
                IVertex firstVertex = edge != null ? edge.GetStartVertex() as IVertex : null;
                IVertex secondVertex = edge != null ? edge.GetEndVertex() as IVertex : null;
                double[] first;
                double[] second;
                if (curve == null || !curve.IsLine() || firstVertex == null ||
                    secondVertex == null || !TryTransformModelPointToView(transform,
                        firstVertex.GetPoint() as double[], out first) ||
                    !TryTransformModelPointToView(transform,
                        secondVertex.GetPoint() as double[], out second))
                    return FailMessage("A symmetry centerline is not attached exclusively to " +
                        "finite linear edges.", out error);
                JArray segment = NormalizedSegment(first, second);
                if (!segments.Any(item => JToken.DeepEquals(item, segment)))
                    segments.Add(segment);
            }
            segments = segments.OrderBy(item => item.ToString(Formatting.None),
                StringComparer.Ordinal).ToList();
            return true;
        }

        private static bool SameCenters(IList<double[]> first, IList<double[]> second)
        {
            if (first.Count != second.Count) return false;
            var remaining = second.ToList();
            foreach (double[] center in first)
            {
                int index = remaining.FindIndex(item =>
                    Distance2(item, center) <= PositionTolerance);
                if (index < 0) return false;
                remaining.RemoveAt(index);
            }
            return true;
        }

        private static bool SameSegments(IList<JArray> first, IList<JArray> second)
        {
            if (first.Count != second.Count) return false;
            List<string> left = first.Select(item => item.ToString(Formatting.None))
                .OrderBy(item => item, StringComparer.Ordinal).ToList();
            List<string> right = second.Select(item => item.ToString(Formatting.None))
                .OrderBy(item => item, StringComparer.Ordinal).ToList();
            return left.SequenceEqual(right, StringComparer.Ordinal);
        }

        private static JArray CenterArray(IEnumerable<double[]> centers)
        {
            return new JArray(centers.OrderBy(item => item[0]).ThenBy(item => item[1])
                .Select(item => new JArray(Quantize(item[0]), Quantize(item[1]))));
        }

        private static JArray SegmentArray(IEnumerable<JArray> segments)
        {
            return new JArray(segments.OrderBy(item => item.ToString(Formatting.None),
                StringComparer.Ordinal).Select(item => item.DeepClone()));
        }

        private bool TryReadCircleProfile(IView sourceView, ISketchSegment segment,
            double expectedOffsetX, double expectedOffsetY, double expectedRadiusSheet,
            out JObject profile, out string error)
        {
            profile = new JObject();
            error = null;
            try
            {
                var curve = segment != null ? segment.GetCurve() as ICurve : null;
                double[] circle = curve != null && curve.IsCircle()
                    ? curve.CircleParams as double[] : null;
                if (segment == null || segment.GetType() != 1 || circle == null ||
                    circle.Length < 7 || circle.Any(item => double.IsNaN(item) ||
                        double.IsInfinity(item)) || circle[6] <= 0.0)
                    return FailMessage("Profile readback is not one finite circular segment.",
                        out error);
                IMathPoint modelPoint = _mathUtility.CreatePoint(
                    new[] { circle[0], circle[1], circle[2] }) as IMathPoint;
                IMathTransform transform = sourceView.ModelToViewTransform;
                double[] transformValues = transform != null
                    ? transform.ArrayData as double[] : null;
                IMathPoint sheetPoint = modelPoint != null && transform != null
                    ? modelPoint.MultiplyTransform(transform) as IMathPoint : null;
                double[] sheet = sheetPoint != null ? sheetPoint.ArrayData as double[] : null;
                double[] position = sourceView.Position as double[];
                if (transformValues == null || transformValues.Length < 13 ||
                    transformValues[12] <= 0.0 || sheet == null || sheet.Length < 2 ||
                    position == null || position.Length < 2)
                    return FailMessage("Circular profile transform readback is unavailable.",
                        out error);
                double offsetX = sheet[0] - position[0];
                double offsetY = sheet[1] - position[1];
                double radiusSheet = circle[6] * transformValues[12];
                if (Math.Abs(offsetX - expectedOffsetX) > PositionTolerance ||
                    Math.Abs(offsetY - expectedOffsetY) > PositionTolerance ||
                    Math.Abs(radiusSheet - expectedRadiusSheet) > PositionTolerance)
                    return FailMessage("Circular profile center or radius differs from the " +
                        "frozen sheet-space definition.", out error);
                profile["center_offset_sheet_m"] = new JArray(
                    Quantize(offsetX), Quantize(offsetY));
                profile["radius_sheet_m"] = Quantize(radiusSheet);
                profile["axis_model"] = new JArray(circle.Skip(3).Take(3).Select(Quantize));
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static bool TryReadSectionContract(IView view, ViewPlanBasicViewSpec spec,
            out JObject section, out string error)
        {
            section = new JObject();
            error = null;
            try
            {
                var data = view.GetSection() as IDrSection;
                if (data == null)
                    return FailMessage("Section readback did not expose IDrSection.", out error);
                int segmentCount = data.IGetLineSegmentCount();
                double[] lineInfo = data.GetLineInfo() as double[];
                int expectedSegments = spec.Type == "full_section" ? 1 :
                    spec.SectionPointsModel.Count - 1;
                if (segmentCount != expectedSegments)
                    return FailMessage("Section cutting-line segment count differs from the plan.",
                        out error);
                if (lineInfo == null || lineInfo.Length != segmentCount * 6 ||
                    lineInfo.Any(item => double.IsNaN(item) || double.IsInfinity(item)))
                    return FailMessage("Section line-info readback is incomplete or non-finite.",
                        out error);
                for (int index = 0; index < segmentCount; index++)
                {
                    int offset = index * 6;
                    double dx = lineInfo[offset + 3] - lineInfo[offset];
                    double dy = lineInfo[offset + 4] - lineInfo[offset + 1];
                    double dz = lineInfo[offset + 5] - lineInfo[offset + 2];
                    if (Math.Sqrt(dx * dx + dy * dy + dz * dz) <= 1e-12)
                        return FailMessage("Section line-info contains a zero-length segment.",
                            out error);
                }
                if (spec.Type != "full_section" &&
                    !SectionLineInfoMatchesFrozenPoints(lineInfo, spec.SectionPointsModel))
                    return FailMessage("Section cutting-line coordinates differ from the " +
                        "frozen model-space points.", out error);
                string label = data.GetLabel();
                bool partial = data.GetPartialSection();
                bool aligned = data.IsAligned();
                bool reversed = data.GetReversedCutDirection();
                int alignment = view.GetAlignment();
                double depth = data.SectionDepth;
                if (!string.Equals(label, spec.SectionLabel, StringComparison.Ordinal))
                    return FailMessage("Section label readback differs from the frozen label.",
                        out error);
                if (partial != (spec.Type == "half_section"))
                    return FailMessage("Section partial/full readback differs from the section type.",
                        out error);
                if (aligned != (spec.Type == "aligned_section"))
                    return FailMessage("Aligned-section readback differs from the section type.",
                        out error);
                if (reversed != spec.SectionReverseDirection)
                    return FailMessage("Section cut-direction readback differs from the plan.",
                        out error);
                if ((spec.Alignment == "not_aligned" && alignment != 0) ||
                    (spec.Alignment == "projected" && alignment == 0))
                    return FailMessage("Section alignment readback differs from the plan.", out error);
                if (double.IsNaN(depth) || double.IsInfinity(depth) || depth <= 0.0)
                    return FailMessage("Section depth readback is not a positive finite value.",
                        out error);
                if (spec.SectionDepth > 0.0 &&
                    Math.Abs(depth - spec.SectionDepth) > Math.Max(1e-9, spec.SectionDepth * 1e-8))
                    return FailMessage("Section depth readback differs from section_depth_m.",
                        out error);

                section["label"] = label;
                section["line_segment_count"] = segmentCount;
                section["line_info_model_m"] = new JArray(lineInfo.Select(Quantize));
                section["partial"] = partial;
                section["aligned"] = aligned;
                section["reversed"] = reversed;
                section["view_alignment"] = alignment;
                section["section_depth_m_actual"] = Quantize(depth);
                section["section_depth_mode"] = spec.SectionDepth == 0.0
                    ? "solidworks_default" : "explicit";
                section["line_geometry_verification"] = spec.Type == "full_section"
                    ? "derived_line_finite" : "exact_frozen_points";
                return true;
            }
            catch (Exception ex)
            {
                return FailMessage(ex.Message, out error);
            }
        }

        private static bool SectionLineInfoMatchesFrozenPoints(double[] lineInfo,
            IList<double[]> points)
        {
            if (lineInfo == null || points == null ||
                lineInfo.Length != (points.Count - 1) * 6)
                return false;
            var matched = new bool[points.Count - 1];
            for (int actualIndex = 0; actualIndex < matched.Length; actualIndex++)
            {
                int offset = actualIndex * 6;
                bool found = false;
                for (int expectedIndex = 0; expectedIndex < matched.Length; expectedIndex++)
                {
                    if (matched[expectedIndex]) continue;
                    double[] first = points[expectedIndex];
                    double[] second = points[expectedIndex + 1];
                    bool forward = CoordinatesEqual(lineInfo, offset, first) &&
                        CoordinatesEqual(lineInfo, offset + 3, second);
                    bool reverse = CoordinatesEqual(lineInfo, offset, second) &&
                        CoordinatesEqual(lineInfo, offset + 3, first);
                    if (!forward && !reverse) continue;
                    matched[expectedIndex] = true;
                    found = true;
                    break;
                }
                if (!found) return false;
            }
            return true;
        }

        private static bool CoordinatesEqual(double[] actual, int offset, double[] expected)
        {
            return actual != null && expected != null && expected.Length >= 3 &&
                offset >= 0 && offset + 2 < actual.Length &&
                Math.Abs(actual[offset] - expected[0]) <= PositionTolerance &&
                Math.Abs(actual[offset + 1] - expected[1]) <= PositionTolerance &&
                Math.Abs(actual[offset + 2] - expected[2]) <= PositionTolerance;
        }

        private static bool TryResolveModelViewNames(IModelDoc2 model,
            out Dictionary<string, string> standard, out HashSet<string> all,
            out string error)
        {
            standard = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            all = new HashSet<string>(StringComparer.Ordinal);
            error = null;
            var raw = model.GetModelViewNames() as Array;
            // SolidWorks 2025 returns *NormalTo followed by the nine swStandardViews_e names,
            // localized to the installation language, followed by user-defined named views.
            if (raw == null || raw.Length < 10)
            {
                error = "GetModelViewNames did not return the normal view plus nine standard views.";
                return false;
            }
            foreach (object item in raw)
            {
                string value = Convert.ToString(item, CultureInfo.InvariantCulture);
                if (!string.IsNullOrWhiteSpace(value)) all.Add(value);
            }
            string[] keys = { "front", "back", "left", "right", "top", "bottom",
                "isometric", "trimetric", "dimetric" };
            for (int index = 0; index < keys.Length; index++)
            {
                string value = Convert.ToString(raw.GetValue(index + 1),
                    CultureInfo.InvariantCulture);
                if (string.IsNullOrWhiteSpace(value))
                {
                    error = "Standard model orientation '" + keys[index] + "' is unavailable.";
                    return false;
                }
                standard.Add(keys[index], value);
            }
            return true;
        }

        private static bool TryConfigureSheet(IModelDoc2 drawingModel, ISheet sheet,
            ViewPlanBasicExecutionPlan plan, out string error)
        {
            error = null;
            if (sheet == null)
            {
                error = "The drawing has no active sheet.";
                return false;
            }
            if (!string.Equals(sheet.GetName(), plan.SheetName, StringComparison.Ordinal))
            {
                error = "Active sheet name differs from the frozen sheet.name.";
                return false;
            }
            var values = sheet.GetProperties2() as double[];
            if (values == null || values.Length < 8)
            {
                error = "GetProperties2 returned an unexpected sheet property array.";
                return false;
            }
            if (Math.Abs(values[5] - plan.SheetWidth) > SheetDimensionTolerance ||
                Math.Abs(values[6] - plan.SheetHeight) > SheetDimensionTolerance)
            {
                error = "Active sheet dimensions differ from the frozen sheet contract.";
                return false;
            }
            bool firstAngle = plan.ProjectionMethod == "first_angle";
            sheet.SetProperties2((int)values[0], (int)values[1], plan.SheetScaleNumerator,
                plan.SheetScaleDenominator, firstAngle, values[5], values[6], values[7] != 0.0);
            if (!sheet.SetScale(plan.SheetScaleNumerator, plan.SheetScaleDenominator,
                false, false))
            {
                error = "ISheet.SetScale rejected the frozen sheet scale.";
                return false;
            }
            // SetProperties2 and SetScale can both read back correctly without making the
            // document save-dirty. Explicitly mark the drawing so the sheet contract survives
            // Save3/close/reopen.
            drawingModel.SetSaveFlag();
            var readback = sheet.GetProperties2() as double[];
            if (readback == null || readback.Length < 8 ||
                Math.Abs(readback[2] - plan.SheetScaleNumerator) > ScaleTolerance ||
                Math.Abs(readback[3] - plan.SheetScaleDenominator) > ScaleTolerance ||
                (readback[4] != 0.0) != firstAngle)
            {
                error = readback == null || readback.Length < 8
                    ? "GetProperties2 returned an unexpected array after SetProperties2."
                    : string.Format(CultureInfo.InvariantCulture,
                        "Sheet projection/scale immediate readback differs: expected {0}:{1}, " +
                        "first_angle={2}; actual {3:R}:{4:R}, first_angle={5}.",
                        plan.SheetScaleNumerator, plan.SheetScaleDenominator, firstAngle,
                        readback[2], readback[3], readback[4] != 0.0);
                return false;
            }
            return true;
        }

        private static bool ContainsModelViews(IDrawingDoc drawing)
        {
            object current = drawing.GetFirstView();
            int guard = 0;
            while (current != null && guard++ < 256)
            {
                var view = current as IView;
                object next = view != null ? view.GetNextView() : null;
                if (view != null && view.Type != 1) return true;
                current = next;
            }
            return false;
        }

        private static bool TryVerifySheetContract(ISheet sheet, ViewPlanBasicExecutionPlan plan,
            out string error)
        {
            error = null;
            if (sheet == null)
            {
                error = "The reopened drawing has no active sheet.";
                return false;
            }
            if (!string.Equals(sheet.GetName(), plan.SheetName, StringComparison.Ordinal))
            {
                error = "Reopened sheet name differs from the frozen contract.";
                return false;
            }
            var values = sheet.GetProperties2() as double[];
            bool firstAngle = plan.ProjectionMethod == "first_angle";
            if (values == null || values.Length < 8 ||
                Math.Abs(values[2] - plan.SheetScaleNumerator) > ScaleTolerance ||
                Math.Abs(values[3] - plan.SheetScaleDenominator) > ScaleTolerance ||
                (values[4] != 0.0) != firstAngle ||
                Math.Abs(values[5] - plan.SheetWidth) > SheetDimensionTolerance ||
                Math.Abs(values[6] - plan.SheetHeight) > SheetDimensionTolerance)
            {
                error = values == null || values.Length < 8
                    ? "Reopened GetProperties2 returned an unexpected sheet property array."
                    : string.Format(CultureInfo.InvariantCulture,
                        "Reopened sheet differs: expected scale {0}:{1}, first_angle={2}, " +
                        "size={3:R}x{4:R} m; actual scale {5:R}:{6:R}, first_angle={7}, " +
                        "size={8:R}x{9:R} m.", plan.SheetScaleNumerator,
                        plan.SheetScaleDenominator, firstAngle, plan.SheetWidth,
                        plan.SheetHeight, values[2], values[3], values[4] != 0.0,
                        values[5], values[6]);
                return false;
            }
            return true;
        }

        private static bool DisplayStateExists(IModelDoc2 model, string configuration,
            string displayState)
        {
            if (string.IsNullOrEmpty(displayState)) return true;
            var config = model.GetConfigurationByName(configuration) as IConfiguration;
            var raw = config != null ? config.GetDisplayStates() as Array : null;
            if (raw == null) return false;
            foreach (object item in raw)
                if (string.Equals(Convert.ToString(item, CultureInfo.InvariantCulture),
                    displayState, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        private static int DisplayModeValue(ViewPlanBasicViewSpec spec)
        {
            if (spec.DisplayMode == "wireframe") return spec.Faceted ? 4 : 0;
            if (spec.DisplayMode == "hidden_lines_visible") return spec.Faceted ? 5 : 1;
            if (spec.DisplayMode == "hidden_lines_removed") return spec.Faceted ? 6 : 2;
            if (spec.DisplayMode == "shaded") return 3;
            if (spec.DisplayMode == "shaded_with_edges") return 7;
            throw new ArgumentOutOfRangeException("DisplayMode");
        }

        private static int TangentEdgeValue(string value)
        {
            if (value == "removed") return 0;
            if (value == "phantom") return 1;
            if (value == "visible") return 2;
            throw new ArgumentOutOfRangeException("value");
        }

        private static int ReadbackDisplayModeValue(ViewPlanBasicViewSpec spec)
        {
            // SolidWorks 2025 writes shaded-with-edges as swSHADED plus the edges flag and reads
            // the mode back as swSHADED. The independent edge bit is verified separately.
            return spec.DisplayMode == "shaded_with_edges" ? 3 : DisplayModeValue(spec);
        }

        private static string SafeUniqueName(IView view)
        {
            try { return view != null ? view.GetUniqueName() : null; }
            catch { return null; }
        }

        private static bool ExplicitRotationMatches(IView view, ViewPlanBasicViewSpec spec)
        {
            // A non-zero drawing roll is verified through IView.Angle above. The underlying
            // model-to-view transform remains the exact temporary basis created from the two
            // model-space direction vectors.
            try
            {
                IMathTransform transform = view.ModelToViewTransform;
                double[] actual = transform != null ? transform.ArrayData as double[] : null;
                double[] expected = BuildExplicitOrientationTransform(spec);
                ApplyDrawingRoll(expected, spec.RollAngleRad);
                if (actual == null || actual.Length < 9) return false;
                for (int index = 0; index < 9; index++)
                    if (Math.Abs(actual[index] - expected[index]) > 1e-9) return false;
                return true;
            }
            catch { return false; }
        }

        private static void ApplyDrawingRoll(double[] transform, double angle)
        {
            if (Math.Abs(angle) <= 1e-15) return;
            double cosine = Math.Cos(angle);
            double sine = Math.Sin(angle);
            // DrawingViewRotate post-multiplies the model-to-view rotation by a sheet-Z rotation:
            // columns X/Y are rotated while the depth column remains unchanged. This convention
            // was reflected and live-verified against SolidWorks 2025 SP5.
            for (int row = 0; row < 3; row++)
            {
                int x = row * 3;
                int y = x + 1;
                double oldX = transform[x];
                double oldY = transform[y];
                transform[x] = cosine * oldX - sine * oldY;
                transform[y] = sine * oldX + cosine * oldY;
            }
        }

        private static bool ModelViewNameExists(IModelDoc2 model, string expected)
        {
            try
            {
                var names = model.GetModelViewNames() as Array;
                if (names == null) return false;
                foreach (object item in names)
                    if (string.Equals(Convert.ToString(item, CultureInfo.InvariantCulture),
                        expected, StringComparison.Ordinal)) return true;
            }
            catch { }
            return false;
        }

        private static double[] Cross(double[] first, double[] second)
        {
            return new[]
            {
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0]
            };
        }

        private static double Dot(double[] first, double[] second)
        {
            return first[0] * second[0] + first[1] * second[1] + first[2] * second[2];
        }

        private static bool IsSectionType(string type)
        {
            return type == "full_section" || type == "half_section" ||
                type == "offset_section" || type == "aligned_section" ||
                type == "removed_section";
        }

        private static bool UsesNativeGeneratedName(string type)
        {
            // SolidWorks owns the visible/internal name of section and detail views. Detail
            // names are derived from the label and scale and IView.SetName2 returns false.
            // Their exact native unique handle is captured before save and compared after reopen.
            return IsSectionType(type) || type == "detail_view" ||
                type == "auxiliary_view";
        }

        private static bool IsModelOrientationType(string type)
        {
            return type == "model_view" || type == "broken_out_section";
        }

        private static bool IsC2Type(string type)
        {
            return type == "broken_out_section" || type == "detail_view";
        }

        private static void Normalize(double[] value)
        {
            double length = Math.Sqrt(value[0] * value[0] + value[1] * value[1] +
                value[2] * value[2]);
            for (int index = 0; index < value.Length; index++) value[index] /= length;
        }

        private static bool ArraysEqual(double[] first, double[] second, double tolerance)
        {
            if (first == null || second == null || first.Length != second.Length) return false;
            for (int index = 0; index < first.Length; index++)
                if (Math.Abs(first[index] - second[index]) > tolerance) return false;
            return true;
        }

        private static double AngularDistance(double first, double second)
        {
            double value = (first - second) % (Math.PI * 2.0);
            if (value > Math.PI) value -= Math.PI * 2.0;
            if (value < -Math.PI) value += Math.PI * 2.0;
            return Math.Abs(value);
        }

        private static double Quantize(double value)
        {
            double rounded = Math.Round(value, 12, MidpointRounding.AwayFromZero);
            return rounded == 0.0 ? 0.0 : rounded;
        }

        private static string AppendError(string first, string second)
        {
            if (string.IsNullOrEmpty(first)) return second;
            if (string.IsNullOrEmpty(second)) return first;
            return first + " " + second;
        }

        private static bool PathEquals(string first, string second)
        {
            if (string.IsNullOrWhiteSpace(first) || string.IsNullOrWhiteSpace(second)) return false;
            try
            {
                return string.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }

        private static bool Fail(string code, string viewId, string message,
            out ViewPlanBasicViewExecutionError error)
        {
            error = new ViewPlanBasicViewExecutionError
            {
                Code = code,
                ViewId = viewId,
                Message = message
            };
            return false;
        }

        private static bool FailMessage(string message, out string error)
        {
            error = message;
            return false;
        }
    }

    internal sealed class AuxiliaryEdgeMatch
    {
        public IEdge Edge { get; set; }
        public double[] Start { get; set; }
        public double[] End { get; set; }
    }

    internal sealed class CenterCircleMatch
    {
        public IEdge Edge { get; set; }
        public double[] CenterSheet { get; set; }
        public string FrozenEdgeId { get; set; }
    }

    internal sealed class CenterLinearEdge
    {
        public IEdge Edge { get; set; }
        public double[] First { get; set; }
        public double[] Second { get; set; }
        public JArray Segment { get; set; }
        public double Perpendicular { get; set; }
        public double AlongMin { get; set; }
        public double AlongMax { get; set; }
    }

    internal sealed class CenterLinePair
    {
        public CenterLinearEdge First { get; set; }
        public CenterLinearEdge Second { get; set; }
        public double PerpendicularSeparation { get; set; }
    }

    internal sealed class ActualCenterMark
    {
        public int Index { get; set; }
        public int Style { get; set; }
        public bool UseDocumentDefaults { get; set; }
        public bool ShowLines { get; set; }
        public int Color { get; set; }
        public List<double[]> Centers { get; set; }
    }

    internal sealed class ActualCenterLine
    {
        public int Index { get; set; }
        public int Color { get; set; }
        public List<JArray> Segments { get; set; }
    }

    internal sealed class ViewPlanBasicViewExecutionResult
    {
        public Dictionary<string, IView> CreatedViews { get; set; }
        public Dictionary<string, string> PersistentHandles { get; set; }
        public Dictionary<string, JObject> SectionFingerprints { get; set; }
        public Dictionary<string, JObject> C2Fingerprints { get; set; }
        public Dictionary<string, JObject> AuxiliaryFingerprints { get; set; }
        public Dictionary<string, JObject> CenterElementFingerprints { get; set; }
        public JObject InMemoryReadback { get; set; }
    }

    internal sealed class ViewPlanBasicViewExecutionError
    {
        public string Code { get; set; }
        public string ViewId { get; set; }
        public string Message { get; set; }
    }
}
