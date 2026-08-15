using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>Native F4/F5 dimension creation and exact in-session/persisted readback.</summary>
    internal sealed class DimensionPlanNativeExecutor
    {
        public bool TryCreate(IModelDoc2 drawingModel, IDrawingDoc drawing,
            IModelDoc2 sourceModel, DimensionPlanExecutionPlan plan,
            out DimensionPlanNativeResult result, out DimensionPlanNativeError error)
        {
            result = null; error = null;
            try
            {
                List<NativeDimensionRecord> baseline = ReadAll(drawingModel, drawing);
                Dictionary<string, double> sourceValues = ReadPlannedSourceValues(
                    sourceModel, plan);
                var baselineNames = new HashSet<string>(baseline.Select(item => item.SelectionName),
                    StringComparer.Ordinal);
                var created = new Dictionary<string, IDisplayDimension>(StringComparer.Ordinal);
                var importedIdentitiesByView = new Dictionary<string, List<string>>(
                    StringComparer.Ordinal);

                if (plan.Dimensions.Any(item => item.ImportModelDimension))
                {
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
                    var aggregateIndexByView = new Dictionary<string, int>(
                        StringComparer.Ordinal);
                    var aggregatesByView = new Dictionary<string,
                        IList<ViewDimensionAggregate>>(StringComparer.Ordinal);
                    foreach (DisplayInView candidate in Enumerate(drawing))
                    {
                        string viewName = candidate.View.Name ?? "";
                        int aggregateIndex;
                        if (!aggregateIndexByView.TryGetValue(viewName, out aggregateIndex))
                            aggregateIndex = 0;
                        IList<ViewDimensionAggregate> aggregates;
                        if (!aggregatesByView.TryGetValue(viewName, out aggregates))
                        {
                            aggregates = ReadViewDimensionAggregates(candidate.View);
                            aggregatesByView.Add(viewName, aggregates);
                        }
                        string aggregateId = aggregateIndex < aggregates.Count
                            ? aggregates[aggregateIndex].DimensionId : "";
                        aggregateIndexByView[viewName] = aggregateIndex + 1;
                        string name = SelectionName(candidate.Display);
                        if (baselineNames.Contains(name)) continue;
                        string fullName = FullName(candidate.Display);
                        List<string> importedIdentities;
                        if (!importedIdentitiesByView.TryGetValue(viewName,
                            out importedIdentities))
                        {
                            importedIdentities = new List<string>();
                            importedIdentitiesByView.Add(viewName, importedIdentities);
                        }
                        string importedIdentity = !String.IsNullOrWhiteSpace(aggregateId)
                            ? aggregateId : fullName;
                        if (!String.IsNullOrWhiteSpace(importedIdentity) &&
                            importedIdentities.Count < 64)
                            importedIdentities.Add(importedIdentity);
                        DimensionPlanExecutionDimension spec = plan.Dimensions.SingleOrDefault(item =>
                            item.ImportModelDimension && item.TargetViewName == viewName &&
                            (item.ModelDimensionFullName == fullName ||
                             AggregateIdentityMatches(aggregateId,
                                 item.ModelDimensionFullName)) &&
                            !created.ContainsKey(item.DimensionId));
                        if (spec != null) created.Add(spec.DimensionId, candidate.Display);
                    }
                    DeleteUnplannedImported(drawingModel, drawing, baselineNames,
                        new HashSet<string>(created.Values.Select(SelectionName), StringComparer.Ordinal));
                }

                foreach (DimensionPlanExecutionDimension spec in plan.Dimensions)
                {
                    IDisplayDimension display;
                    if (!created.TryGetValue(spec.DimensionId, out display))
                    {
                        if (spec.ImportModelDimension)
                        {
                            List<string> importedIdentities;
                            importedIdentitiesByView.TryGetValue(spec.TargetViewName,
                                out importedIdentities);
                            string diagnostic = importedIdentities == null ||
                                importedIdentities.Count == 0
                                ? "none"
                                : String.Join(", ", importedIdentities.Distinct(
                                    StringComparer.Ordinal));
                            return Fail("DIMENSION_MODEL_IMPORT_MISSING", spec.DimensionId,
                                "The planned model dimension was not imported into its target view. " +
                                "Imported identities in target view: " + diagnostic + ".",
                                out error);
                        }
                        IView view = FindView(drawing, spec.TargetViewName);
                        if (view == null)
                            return Fail("DIMENSION_TARGET_VIEW_MISSING", spec.DimensionId,
                                "Target view is missing: " + spec.TargetViewName, out error);
                        drawingModel.ClearSelection2(true);
                        foreach (DimensionPlanExecutionAttachment attachment in spec.Attachments)
                        {
                            string diagnostic;
                            if (!TrySelectAttachment(view, sourceModel,
                                spec.TargetViewId, attachment, out diagnostic))
                                return Fail("DIMENSION_ATTACHMENT_RESOLUTION_FAILED", spec.DimensionId,
                                    "Could not resolve and select attachment " +
                                    attachment.AttachmentId + "; " + diagnostic +
                                    ", view=" + spec.TargetViewName + ".",
                                    out error);
                        }
                        object value;
                        if (spec.UseOrdinate)
                        {
                            display = CreateOrdinate(drawingModel, drawing, spec);
                            value = display;
                        }
                        else if (spec.Kind == "diameter" || spec.Kind == "boss")
                            value = drawingModel.AddDiameterDimension2(spec.PositionX, spec.PositionY, 0);
                        else if (spec.Kind == "radius" || spec.Kind == "fillet")
                            value = drawingModel.AddRadialDimension2(spec.PositionX, spec.PositionY, 0);
                        else if (IsHoleCalloutKind(spec.Kind))
                            value = drawing.AddHoleCallout2(spec.PositionX, spec.PositionY, 0);
                        else if (spec.Kind == "chamfer")
                            value = drawing.AddChamferDim(spec.PositionX, spec.PositionY, 0);
                        else if (spec.Kind == "symmetric")
                            value = drawingModel.Extension.AddSymmetricDimension(
                                spec.PositionX, spec.PositionY, 0);
                        else
                            value = drawingModel.AddDimension2(spec.PositionX, spec.PositionY, 0);
                        display = value as IDisplayDimension;
                        if (display == null)
                            return Fail("DIMENSION_NATIVE_CREATE_FAILED", spec.DimensionId,
                                "The native SolidWorks dimension API returned no display dimension.",
                                out error);
                        created.Add(spec.DimensionId, display);
                    }
                    if (!ApplyFormat(display, spec, out error)) return false;
                }

                drawingModel.ClearSelection2(true);
                drawingModel.ForceRebuild3(false);
                List<NativeDimensionRecord> memory = ReadAll(drawingModel, drawing);
                PopulateImportedModelValues(plan, memory, sourceModel, drawing,
                    sourceValues);
                Dictionary<string, string> handles;
                Dictionary<string, string> fingerprints;
                JObject verification;
                if (!Verify(plan, baseline.Count, memory, null, null, out handles,
                    out fingerprints,
                    out verification, out error)) return false;
                result = new DimensionPlanNativeResult
                    { BaselineCount = baseline.Count, Handles = handles,
                        PersistenceFingerprints = fingerprints,
                        InMemoryVerification = verification };
                return true;
            }
            catch (Exception ex)
            {
                return Fail("DIMENSION_NATIVE_EXECUTION_FAILED", "", ex.Message, out error);
            }
        }

        public bool TryVerifyPersisted(IModelDoc2 drawingModel, IDrawingDoc drawing,
            DimensionPlanExecutionPlan plan, int baselineCount,
            IDictionary<string, string> expectedHandles,
            IDictionary<string, string> expectedFingerprints, out JObject verification,
            out DimensionPlanNativeError error)
        {
            Dictionary<string, string> ignored;
            Dictionary<string, string> ignoredFingerprints;
            verification = null; error = null;
            COMException lastServerFault = null;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    List<NativeDimensionRecord> records = ReadAll(drawingModel, drawing);
                    PopulateImportedModelValues(plan, records, null, drawing, null);
                    return Verify(plan, baselineCount, records,
                        expectedHandles, expectedFingerprints, out ignored,
                        out ignoredFingerprints, out verification, out error);
                }
                catch (COMException ex)
                {
                    // SolidWorks 2025 SP5 can raise a transient RPC_E_SERVERFAULT while a
                    // just-saved drawing is first traversed after read-only reopen.  Retry the
                    // complete deterministic snapshot, never an individual field, so evidence
                    // is accepted only from one coherent pass.
                    if ((uint)ex.ErrorCode != 0x80010105U) throw;
                    lastServerFault = ex;
                    try { drawingModel.ForceRebuild3(false); } catch { }
                }
            }
            return Fail("DIMENSION_PERSISTED_READBACK_UNAVAILABLE", "",
                "SolidWorks returned RPC_E_SERVERFAULT for three complete readback attempts: " +
                (lastServerFault != null ? lastServerFault.Message : "unknown COM error"),
                out error);
        }

        private static bool ApplyFormat(IDisplayDimension display,
            DimensionPlanExecutionDimension spec, out DimensionPlanNativeError error)
        {
            error = null;
            try
            {
                IAnnotation annotation = display.GetAnnotation() as IAnnotation;
                if (annotation == null || !annotation.SetPosition(spec.PositionX, spec.PositionY, 0))
                    return Fail("DIMENSION_POSITION_WRITE_FAILED", spec.DimensionId,
                        "SolidWorks rejected the frozen initial position.", out error);
                display.SetText((int)swDimensionTextParts_e.swDimensionTextPrefix, spec.Prefix);
                display.SetText((int)swDimensionTextParts_e.swDimensionTextSuffix, spec.Suffix);
                bool useDocumentUnits = spec.Unit == "document_default" || spec.Unit == "count";
                int unit = spec.Unit == "inch" ? (int)swLengthUnit_e.swINCHES : 0;
                display.SetUnits(useDocumentUnits, unit, 0, 0, false);
                display.SetPrecision3(spec.Precision, spec.Precision, spec.Precision, spec.Precision);
                display.ShowParenthesis = spec.ShowParentheses;
                display.DisplayAsChain = spec.ChainId != null;
                IDimension dimension = display.GetDimension2(0) as IDimension;
                if (spec.Kind == "reference")
                {
                    if (dimension == null)
                        return Fail("DIMENSION_REFERENCE_STATE_FAILED", spec.DimensionId,
                            "Reference dimension has no native IDimension.", out error);
                    dimension.DrivenState = (int)swDimensionDrivenState_e.swDimensionDriven;
                }
                if (!ApplyTolerance(dimension, spec, out error)) return false;
                return true;
            }
            catch (Exception ex)
            {
                return Fail("DIMENSION_FORMAT_WRITE_FAILED", spec.DimensionId, ex.Message, out error);
            }
        }

        private static void PopulateImportedModelValues(DimensionPlanExecutionPlan plan,
            IList<NativeDimensionRecord> records, IModelDoc2 sourceModel,
            IDrawingDoc drawing, IDictionary<string, double> sourceValues)
        {
            foreach (DimensionPlanExecutionDimension spec in plan.Dimensions.Where(
                item => item.ImportModelDimension))
            {
                NativeDimensionRecord record = records.SingleOrDefault(item =>
                    item.ViewName == spec.TargetViewName &&
                    (item.FullName == spec.ModelDimensionFullName ||
                     AggregateIdentityMatches(item.AggregateDimensionId,
                         spec.ModelDimensionFullName)));
                if (record == null) continue;
                if (IsFinite(record.ValueSi) &&
                    (record.ValueSi > 0 || spec.NominalSi == 0)) continue;
                IModelDoc2 referenced = sourceModel;
                if (referenced == null)
                {
                    IView view = FindView(drawing, spec.TargetViewName);
                    referenced = view != null ? view.ReferencedDocument as IModelDoc2 : null;
                }
                double sourceValue;
                if (sourceValues != null && sourceValues.TryGetValue(
                    spec.DimensionId, out sourceValue) && IsFinite(sourceValue))
                {
                    record.ValueSi = sourceValue;
                    continue;
                }
                if (referenced == null) continue;
                try
                {
                    IDimension sourceDimension = FindModelDimension(referenced,
                        spec.ModelDimensionFullName);
                    if (sourceDimension == null) continue;
                    record.ValueSi = ReadModelDimensionValue(sourceDimension);
                }
                catch { }
            }
        }

        private static Dictionary<string, double> ReadPlannedSourceValues(
            IModelDoc2 sourceModel, DimensionPlanExecutionPlan plan)
        {
            var result = new Dictionary<string, double>(StringComparer.Ordinal);
            if (sourceModel == null) return result;
            foreach (DimensionPlanExecutionDimension spec in plan.Dimensions.Where(
                item => item.ImportModelDimension))
            {
                IDimension dimension = FindModelDimension(sourceModel,
                    spec.ModelDimensionFullName);
                double value = ReadModelDimensionValue(dimension);
                if (IsFinite(value)) result[spec.DimensionId] = value;
            }
            return result;
        }

        private static bool Verify(DimensionPlanExecutionPlan plan, int baselineCount,
            IList<NativeDimensionRecord> records, IDictionary<string, string> expectedHandles,
            IDictionary<string, string> expectedFingerprints,
            out Dictionary<string, string> handles,
            out Dictionary<string, string> fingerprints, out JObject snapshot,
            out DimensionPlanNativeError error)
        {
            handles = new Dictionary<string, string>(StringComparer.Ordinal);
            fingerprints = new Dictionary<string, string>(StringComparer.Ordinal);
            snapshot = new JObject { ["verified"] = false, ["actual_total_count"] = records.Count,
                ["baseline_count"] = baselineCount, ["planned_count"] = plan.Dimensions.Count };
            error = null;
            if (records.Count != baselineCount + plan.Dimensions.Count)
                return Fail("DIMENSION_UNPLANNED_OR_PARTIAL", "",
                    "Actual dimension count is not baseline + planned count.", out error);
            var rows = new JArray();
            foreach (DimensionPlanExecutionDimension spec in plan.Dimensions)
            {
                NativeDimensionRecord record = null;
                string expectedHandle;
                if (expectedHandles != null && expectedHandles.TryGetValue(spec.DimensionId,
                    out expectedHandle))
                    record = records.SingleOrDefault(item => item.SelectionName == expectedHandle);
                if (record == null && spec.ImportModelDimension)
                    record = records.SingleOrDefault(item => item.ViewName == spec.TargetViewName &&
                        (item.FullName == spec.ModelDimensionFullName ||
                        AggregateIdentityMatches(item.AggregateDimensionId,
                            spec.ModelDimensionFullName)));
                if (record == null && expectedHandles == null)
                    record = records.Where(item => item.ViewName == spec.TargetViewName &&
                        NativeTypeMatches(item, spec.Kind) &&
                        Math.Abs(item.PositionX - spec.PositionX) <= spec.PositionTolerance &&
                        Math.Abs(item.PositionY - spec.PositionY) <= spec.PositionTolerance)
                        .SingleOrDefault();
                if (record == null)
                    return Fail("DIMENSION_IDENTITY_MISMATCH", spec.DimensionId,
                        "No unique dimension matches the frozen identity.", out error);
                if (string.IsNullOrWhiteSpace(record.SelectionName))
                    return Fail("DIMENSION_IDENTITY_UNSTABLE", spec.DimensionId,
                        "SolidWorks returned an empty persistent selection name.", out error);
                if (record.ViewName != spec.TargetViewName || !NativeTypeMatches(record, spec.Kind))
                    return Fail("DIMENSION_TYPE_OR_VIEW_MISMATCH", spec.DimensionId,
                        "Native type or target view differs from the plan.", out error);
                if (!IsFinite(record.PositionX) || !IsFinite(record.PositionY))
                    return Fail("DIMENSION_POSITION_MISMATCH", spec.DimensionId,
                        "Native annotation position is unavailable or non-finite.", out error);
                if (spec.Kind != "hole_quantity" && !IsFinite(record.ValueSi))
                    return Fail("DIMENSION_VALUE_MISMATCH", spec.DimensionId,
                        "Native dimension value is unavailable or non-finite.", out error);
                if (spec.Kind != "hole_quantity" &&
                    Math.Abs(record.ValueSi - spec.NominalSi) > spec.ValueTolerance)
                    return Fail("DIMENSION_VALUE_MISMATCH", spec.DimensionId,
                        "Native value differs from the frozen nominal: expected=" +
                        spec.NominalSi.ToString("R", CultureInfo.InvariantCulture) +
                        ", actual=" + record.ValueSi.ToString("R",
                            CultureInfo.InvariantCulture) + ".", out error);
                if (Math.Abs(record.PositionX - spec.PositionX) > spec.PositionTolerance ||
                    Math.Abs(record.PositionY - spec.PositionY) > spec.PositionTolerance)
                    return Fail("DIMENSION_POSITION_MISMATCH", spec.DimensionId,
                        "Native annotation position differs from the frozen position.", out error);
                if (record.Prefix != spec.Prefix || record.Suffix != spec.Suffix)
                    return Fail("DIMENSION_TEXT_MISMATCH", spec.DimensionId,
                        "Native prefix or suffix differs from the plan: expected prefix=" +
                        JsonString(spec.Prefix) + ", actual prefix=" + JsonString(record.Prefix) +
                        ", expected suffix=" + JsonString(spec.Suffix) + ", actual suffix=" +
                        JsonString(record.Suffix) + ".", out error);
                string expectedFingerprint;
                if (expectedFingerprints != null && expectedFingerprints.TryGetValue(
                    spec.DimensionId, out expectedFingerprint) &&
                    record.PersistenceFingerprint() != expectedFingerprint)
                    return Fail("DIMENSION_TEXT_MISMATCH", spec.DimensionId,
                        "Native text, hole variables or tolerance changed across save/reopen.",
                        out error);
                if (spec.Kind == "hole_quantity")
                {
                    string count = Math.Round(spec.NominalSi).ToString(
                        CultureInfo.InvariantCulture);
                    string renderedText = (record.Prefix ?? "") +
                        (record.AllText ?? "") + (record.Suffix ?? "");
                    if (!Regex.IsMatch(renderedText, @"(?<!\d)" +
                        Regex.Escape(count) + @"(?!\d)", RegexOptions.CultureInvariant))
                        return Fail("DIMENSION_QUANTITY_TEXT_MISMATCH", spec.DimensionId,
                            "Hole callout text does not contain the frozen quantity.", out error);
                }
                if (record.Precision != spec.Precision)
                    return Fail("DIMENSION_PRECISION_MISMATCH", spec.DimensionId,
                        "Native primary precision differs from the plan.", out error);
                if (record.ShowParentheses != spec.ShowParentheses)
                    return Fail("DIMENSION_PARENTHESES_MISMATCH", spec.DimensionId,
                        "Native parenthesis display differs from the plan.", out error);
                if (record.DisplayAsChain != (spec.ChainId != null))
                    return Fail("DIMENSION_CHAIN_DISPLAY_MISMATCH", spec.DimensionId,
                        "Native chain display differs from DimensionPlan hierarchy.", out error);
                bool expectedDocumentUnits = spec.Unit == "document_default" || spec.Unit == "count";
                int expectedUnit = spec.Unit == "inch" ? (int)swLengthUnit_e.swINCHES : 0;
                if (spec.Unit != "count" && (record.UseDocumentUnits != expectedDocumentUnits ||
                    (!expectedDocumentUnits && record.Unit != expectedUnit)))
                    return Fail("DIMENSION_UNIT_MISMATCH", spec.DimensionId,
                        "Native display units differ from the plan.", out error);
                if (spec.Kind == "reference" && record.DrivenState !=
                    (int)swDimensionDrivenState_e.swDimensionDriven)
                    return Fail("DIMENSION_REFERENCE_STATE_MISMATCH", spec.DimensionId,
                        "The planned reference dimension is not native driven/reference state.", out error);
                if (!ToleranceMatches(record.Tolerance, spec.Tolerance))
                    return Fail("DIMENSION_TOLERANCE_MISMATCH", spec.DimensionId,
                        "Native tolerance values/fit differ from the trusted plan.", out error);
                var expectedRefs = spec.Attachments.Select(item => item.PersistentReference)
                    .OrderBy(item => item, StringComparer.Ordinal).ToArray();
                var actualRefs = record.ModelPersistentReferences.OrderBy(item => item,
                    StringComparer.Ordinal).ToArray();
                if (!expectedRefs.SequenceEqual(actualRefs, StringComparer.Ordinal))
                    return Fail("DIMENSION_ATTACHMENT_MISMATCH", spec.DimensionId,
                        "Attached model persistent references differ from the plan.", out error);
                handles.Add(spec.DimensionId, record.SelectionName);
                fingerprints.Add(spec.DimensionId, record.PersistenceFingerprint());
                rows.Add(record.ToJson(spec.DimensionId));
            }
            snapshot["dimensions"] = rows;
            snapshot["verified"] = true;
            return true;
        }

        private static IDisplayDimension CreateOrdinate(IModelDoc2 model, IDrawingDoc drawing,
            DimensionPlanExecutionDimension spec)
        {
            var before = new HashSet<string>(Enumerate(drawing).Select(item =>
                SelectionName(item.Display)), StringComparer.Ordinal);
            int createStatus;
            try
            {
                createStatus = drawing.AddOrdinateDimension2(
                    spec.OrdinateType, spec.PositionX, spec.PositionY, 0);
            }
            finally
            {
                model.SetPickMode();
            }
            if (createStatus != (int)swCreateOrdDimError_e.swCreateOrdDimErr_Success)
                throw new InvalidOperationException(
                    "Native ordinate creation failed with status " + createStatus + ".");
            model.ForceRebuild3(false);
            IDisplayDimension[] added = Enumerate(drawing).Select(item => item.Display).Where(
                item => !before.Contains(SelectionName(item))).ToArray();
            if (added.Length != 1)
                throw new InvalidOperationException("Native ordinate creation did not add exactly one dimension.");
            return added[0];
        }

        private static bool ApplyTolerance(IDimension dimension,
            DimensionPlanExecutionDimension spec, out DimensionPlanNativeError error)
        {
            error = null;
            if (dimension == null)
                return spec.Tolerance == null || Fail("DIMENSION_TOLERANCE_WRITE_FAILED",
                    spec.DimensionId, "Native dimension has no tolerance object.", out error);
            IDimensionTolerance tolerance = dimension.Tolerance as IDimensionTolerance;
            if (tolerance == null)
                return spec.Tolerance == null || Fail("DIMENSION_TOLERANCE_WRITE_FAILED",
                    spec.DimensionId, "Native dimension has no IDimensionTolerance.", out error);
            if (spec.Tolerance == null)
            {
                tolerance.Type = (int)swTolType_e.swTolNONE;
                return true;
            }
            bool written;
            if (spec.Tolerance.Kind == "fit")
            {
                tolerance.Type = (int)swTolType_e.swTolFIT;
                written = spec.FitTarget == "hole"
                    ? tolerance.SetFitValues(spec.Tolerance.FitCode, "")
                    : tolerance.SetFitValues("", spec.Tolerance.FitCode);
            }
            else
            {
                tolerance.Type = spec.Tolerance.Kind == "limit"
                    ? (int)swTolType_e.swTolLIMIT : (int)swTolType_e.swTolBILAT;
                written = tolerance.SetValues2(spec.Tolerance.LowerSi.Value,
                    spec.Tolerance.UpperSi.Value,
                    (int)swSetValueInConfiguration_e.swSetValue_InAllConfigurations, null);
            }
            if (!written)
                return Fail("DIMENSION_TOLERANCE_WRITE_FAILED", spec.DimensionId,
                    "SolidWorks rejected the trusted tolerance values.", out error);
            return true;
        }

        private static bool ToleranceMatches(NativeToleranceRecord actual,
            DimensionPlanExecutionTolerance expected)
        {
            if (actual == null) return expected == null;
            if (expected == null) return actual.Type == (int)swTolType_e.swTolNONE;
            if (expected.Kind == "fit")
                return new[] { (int)swTolType_e.swTolFIT,
                    (int)swTolType_e.swTolFITWITHTOL,
                    (int)swTolType_e.swTolFITTOLONLY }.Contains(actual.Type) &&
                    (actual.HoleFit == expected.FitCode || actual.ShaftFit == expected.FitCode);
            int expectedType = expected.Kind == "limit" ? (int)swTolType_e.swTolLIMIT :
                (int)swTolType_e.swTolBILAT;
            return actual.Type == expectedType && actual.MinimumValid && actual.MaximumValid &&
                Math.Abs(actual.Minimum - expected.LowerSi.Value) <= 1e-12 &&
                Math.Abs(actual.Maximum - expected.UpperSi.Value) <= 1e-12;
        }

        private static bool NativeTypeMatches(NativeDimensionRecord record, string kind)
        {
            if (IsHoleCalloutKind(kind)) return record.IsHoleCallout;
            if (kind == "hole_group_location")
                return record.Type == (int)swDimensionType_e.swOrdinateDimension ||
                    record.Type == (int)swDimensionType_e.swHorOrdinateDimension ||
                    record.Type == (int)swDimensionType_e.swVertOrdinateDimension;
            if (kind == "diameter" || kind == "boss")
                return record.Type == (int)swDimensionType_e.swDiameterDimension ||
                record.Type == (int)swDimensionType_e.swDiametricLinearDimension;
            if (kind == "radius" || kind == "fillet")
                return record.Type == (int)swDimensionType_e.swRadialDimension ||
                record.Type == (int)swDimensionType_e.swRadialLinearDimension;
            if (kind == "angular") return record.Type == (int)swDimensionType_e.swAngularDimension;
            if (kind == "chamfer")
                return record.Type == (int)swDimensionType_e.swChamferDimension;
            if (kind == "linear" || kind == "aligned" || kind == "reference" ||
                kind == "hole_spacing" || kind == "overall" || kind == "step" ||
                kind == "slot")
                return record.Type == (int)swDimensionType_e.swLinearDimension ||
                    record.Type == (int)swDimensionType_e.swHorLinearDimension ||
                    record.Type == (int)swDimensionType_e.swVertLinearDimension;
            if (kind == "symmetric")
                return record.Type == (int)swDimensionType_e.swDiametricLinearDimension ||
                    record.Type == (int)swDimensionType_e.swLinearDimension;
            return false;
        }

        private static bool AggregateIdentityMatches(string aggregateId,
            string modelDimensionFullName)
        {
            if (String.IsNullOrWhiteSpace(aggregateId) ||
                String.IsNullOrWhiteSpace(modelDimensionFullName)) return false;
            if (String.Equals(aggregateId, modelDimensionFullName,
                StringComparison.Ordinal)) return true;
            string modelKey = modelDimensionFullName.EndsWith(".Part",
                StringComparison.Ordinal)
                ? modelDimensionFullName.Substring(0,
                    modelDimensionFullName.Length - ".Part".Length)
                : modelDimensionFullName;
            return aggregateId.StartsWith(modelKey + "-", StringComparison.Ordinal);
        }

        private static bool IsHoleCalloutKind(string kind) =>
            kind == "hole_diameter" || kind == "hole_depth" || kind == "hole_quantity";

        private static bool TrySelectAttachment(IView view, IModelDoc2 sourceModel,
            string targetViewId, DimensionPlanExecutionAttachment attachment,
            out string diagnostic)
        {
            diagnostic = "source_reference_unresolved";
            try
            {
                int state;
                object entity = sourceModel.Extension.GetObjectByPersistReference3(
                    Convert.FromBase64String(attachment.PersistentReference), out state);
                if (entity != null && state == 0 && view.SelectEntity(entity, true))
                    return true;
                diagnostic = "state=" + state + ", resolved_type=" +
                    (entity != null ? entity.GetType().FullName : "null");
            }
            catch (Exception ex)
            {
                diagnostic = "source_reference_error=" + ex.Message;
            }
            int ordinaryIndex = 0;
            Array components = view.GetVisibleComponents() as Array;
            if (components == null) return false;
            foreach (object componentObject in components)
            {
                var component = componentObject as Component2;
                Array entities = component != null
                    ? view.GetVisibleEntities2(component,
                        (int)swViewEntityType_e.swViewEntityType_Edge) as Array
                    : null;
                if (entities == null) continue;
                foreach (object candidate in entities)
                {
                    byte[] bytes = null;
                    try
                    {
                        bytes = ToBytes(sourceModel.Extension
                            .GetPersistReference3(candidate));
                    }
                    catch { }
                    if (bytes == null || bytes.Length == 0) continue;
                    string persist = Convert.ToBase64String(bytes);
                    string entityId = "GE-" + StableToken(targetViewId +
                        "|entity|" + ordinaryIndex.ToString(
                            CultureInfo.InvariantCulture) + "|" + persist);
                    ordinaryIndex++;
                    if (!String.Equals(entityId, attachment.EntityId,
                        StringComparison.Ordinal) ||
                        !String.Equals(persist, attachment.PersistentReference,
                            StringComparison.Ordinal)) continue;
                    if (view.SelectEntity(candidate, true)) return true;
                    diagnostic = "drawing_context_entity_selection_failed";
                    return false;
                }
            }
            diagnostic += ", drawing_context_entity_not_found";
            return false;
        }

        private static string StableToken(string value)
        {
            using (var sha = SHA256.Create())
                return String.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value ?? ""))
                    .Take(8).Select(item => item.ToString("x2",
                        CultureInfo.InvariantCulture)));
        }

        private static void DeleteUnplannedImported(IModelDoc2 model, IDrawingDoc drawing,
            ISet<string> baselineNames, ISet<string> retainedNames)
        {
            foreach (DisplayInView item in Enumerate(drawing).ToList())
            {
                string name = SelectionName(item.Display);
                if (baselineNames.Contains(name) || retainedNames.Contains(name)) continue;
                IAnnotation annotation = item.Display.GetAnnotation() as IAnnotation;
                model.ClearSelection2(true);
                if (annotation == null || !annotation.Select3(false, null))
                    throw new InvalidOperationException("Could not delete an unplanned imported dimension.");
                model.EditDelete();
            }
            model.ClearSelection2(true);
        }

        private static List<NativeDimensionRecord> ReadAll(IModelDoc2 model, IDrawingDoc drawing)
        {
            var result = new List<NativeDimensionRecord>();
            var countByView = new Dictionary<string, int>(StringComparer.Ordinal);
            var aggregatesByView = new Dictionary<string, IList<ViewDimensionAggregate>>(
                StringComparer.Ordinal);
            foreach (DisplayInView item in Enumerate(drawing))
            {
                IDisplayDimension display = item.Display;
                string viewName = item.View.Name ?? "";
                int aggregateIndex;
                if (!countByView.TryGetValue(viewName, out aggregateIndex))
                    aggregateIndex = 0;
                IList<ViewDimensionAggregate> aggregates;
                if (!aggregatesByView.TryGetValue(viewName, out aggregates))
                {
                    aggregates = ReadViewDimensionAggregates(item.View);
                    aggregatesByView.Add(viewName, aggregates);
                }
                ViewDimensionAggregate aggregate = aggregateIndex < aggregates.Count
                    ? aggregates[aggregateIndex] : null;
                countByView[viewName] = aggregateIndex + 1;
                IModelDoc2 referenced = item.View.ReferencedDocument as IModelDoc2;
                IDimension dimension = null;
                try { dimension = display.GetDimension2(0) as IDimension; }
                catch (COMException ex)
                {
                    if ((uint)ex.ErrorCode != 0x80010105U) throw;
                }
                IAnnotation annotation = display.GetAnnotation() as IAnnotation;
                double[] position = annotation != null ? annotation.GetPosition() as double[] : null;
                double value = double.NaN;
                if (dimension != null)
                {
                    double viewContextValue = double.NaN;
                    try
                    {
                        object raw = dimension.GetSystemValue3(1, null);
                        Array values = raw as Array;
                        if (values != null && values.Length > 0)
                            viewContextValue = Convert.ToDouble(values.GetValue(0),
                                CultureInfo.InvariantCulture);
                    }
                    catch { }
                    // GetSystemValue3 is the primary SI readback. IDimension.Value is document-unit
                    // scaled and cannot be compared with a frozen SI nominal. Some imported model
                    // dimensions return zero from the drawing-view context after reopen; only then
                    // attempt SystemValue. Deferring that fallback matters because SolidWorks 2025
                    // SP5 can raise RPC_E_SERVERFAULT from SystemValue on an otherwise healthy
                    // read-only reopened drawing.
                    if (!IsFinite(value) && IsFinite(viewContextValue) && viewContextValue > 0)
                        value = viewContextValue;
                    else if (!IsFinite(value))
                    {
                        try { value = dimension.SystemValue; }
                        catch { value = double.NaN; }
                    }
                }
                if (!IsFinite(value) && aggregate != null && referenced != null)
                    value = ReadReferencedModelValue(referenced, viewName,
                        aggregate.DimensionId);
                if (!IsFinite(value) && aggregate != null &&
                    IsFinite(aggregate.ValueSi) && aggregate.ValueSi > 0)
                    value = aggregate.ValueSi;
                string fullName = "";
                int drivenState = 0;
                if (dimension != null)
                {
                    try { fullName = dimension.FullName ?? ""; } catch { }
                    try { drivenState = dimension.DrivenState; } catch { }
                }
                var refs = new List<string>();
                Array attached = annotation != null ? annotation.GetAttachedEntities3() as Array : null;
                if (attached != null && referenced != null)
                    foreach (object entity in attached)
                    {
                        if (entity == null) continue;
                        byte[] bytes = ToBytes(referenced.Extension.GetPersistReference3(entity));
                        if (bytes != null && bytes.Length > 0) refs.Add(Convert.ToBase64String(bytes));
                    }
                int nativeType = display.Type2;
                string prefix = display.GetText(
                    (int)swDimensionTextParts_e.swDimensionTextPrefix) ?? "";
                string allText = display.GetText(
                    (int)swDimensionTextParts_e.swDimensionTextAll) ?? "";
                if (IsAutomaticDiameterModifier(nativeType, prefix))
                {
                    // SolidWorks can materialize its implicit diameter glyph as <MOD-DIAM>
                    // only after a new-process reopen. It is native type semantics, not a
                    // user-authored DimensionPlan prefix, so canonicalize it out of both the
                    // logical text fields and the persistence fingerprint.
                    prefix = "";
                    if (allText == "<MOD-DIAM>") allText = "";
                }
                result.Add(new NativeDimensionRecord
                {
                    ViewName = item.View.Name ?? "", SelectionName = SelectionName(display),
                    FullName = fullName,
                    AggregateDimensionId = aggregate != null ? aggregate.DimensionId : "",
                    Type = nativeType, IsHoleCallout = display.IsHoleCallout(), ValueSi = value,
                    PositionX = position != null && position.Length > 0 ? position[0] : double.NaN,
                    PositionY = position != null && position.Length > 1 ? position[1] : double.NaN,
                    Prefix = prefix,
                    Suffix = display.GetText((int)swDimensionTextParts_e.swDimensionTextSuffix) ?? "",
                    AllText = allText,
                    Precision = display.GetPrimaryPrecision(),
                    Unit = display.GetUnits(), UseDocumentUnits = display.GetUseDocUnits(),
                    ShowParentheses = display.ShowParenthesis,
                    DisplayAsChain = display.DisplayAsChain,
                    DrivenState = drivenState,
                    Tolerance = ReadToleranceSafe(dimension),
                    HoleCalloutVariables = ReadHoleCalloutVariables(display),
                    ModelPersistentReferences = refs
                });
            }
            return result;
        }

        private static IList<ViewDimensionAggregate> ReadViewDimensionAggregates(IView view)
        {
            var result = new List<ViewDimensionAggregate>();
            int count = view.GetDimensionCount4();
            if (count == 0) return result;
            string[] ids = Strings(view.GetDimensionIds4());
            double[] info = Doubles(view.GetDimensionInfo7());
            const int recordSize = 52;
            const int dimensionValueOffset = 47;
            if (ids.Length != count || info.Length != 1 + count * recordSize ||
                Convert.ToInt32(info[0], CultureInfo.InvariantCulture) != count)
                throw new InvalidOperationException(
                    "SolidWorks view dimension aggregate arrays are inconsistent.");
            for (int index = 0; index < count; index++)
                result.Add(new ViewDimensionAggregate
                {
                    DimensionId = ids[index] ?? "",
                    ValueSi = info[1 + index * recordSize + dimensionValueOffset]
                });
            return result;
        }

        private static double ReadReferencedModelValue(IModelDoc2 referenced,
            string viewName, string aggregateId)
        {
            if (referenced == null || String.IsNullOrWhiteSpace(viewName) ||
                String.IsNullOrWhiteSpace(aggregateId)) return double.NaN;
            Match match = Regex.Match(aggregateId,
                "^(.*)-[0-9]+@" + Regex.Escape(viewName) + "$",
                RegexOptions.CultureInvariant);
            if (!match.Success) return double.NaN;
            string fullName = match.Groups[1].Value + ".Part";
            try
            {
                IDimension sourceDimension = FindModelDimension(referenced, fullName);
                if (sourceDimension == null) return double.NaN;
                return ReadModelDimensionValue(sourceDimension);
            }
            catch { return double.NaN; }
        }

        private static double ReadModelDimensionValue(IDimension dimension)
        {
            if (dimension == null) return double.NaN;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    object raw = dimension.GetSystemValue3(1, null);
                    Array values = raw as Array;
                    if (values != null && values.Length > 0)
                    {
                        double value = Convert.ToDouble(values.GetValue(0),
                            CultureInfo.InvariantCulture);
                        if (IsFinite(value)) return value;
                    }
                }
                catch { }
                try
                {
                    double value = dimension.SystemValue;
                    if (IsFinite(value)) return value;
                }
                catch { }
                try
                {
                    double value = dimension.GetSystemValue2("");
                    if (IsFinite(value)) return value;
                }
                catch { }
            }
            return double.NaN;
        }

        private static IDimension FindModelDimension(IModelDoc2 model,
            string fullName)
        {
            if (model == null || String.IsNullOrWhiteSpace(fullName)) return null;
            IFeature feature = model.FirstFeature() as IFeature;
            int featureGuard = 0;
            while (feature != null && featureGuard++ < 100000)
            {
                object current = null;
                try { current = feature.GetFirstDisplayDimension(); } catch { }
                int dimensionGuard = 0;
                while (current != null && dimensionGuard++ < 10000)
                {
                    IDisplayDimension display = current as IDisplayDimension;
                    object next = null;
                    try { next = feature.GetNextDisplayDimension(current); } catch { }
                    try
                    {
                        IDimension dimension = display != null
                            ? display.GetDimension2(0) as IDimension : null;
                        if (dimension != null && String.Equals(dimension.FullName,
                            fullName, StringComparison.Ordinal)) return dimension;
                    }
                    catch { }
                    current = next;
                }
                feature = feature.GetNextFeature() as IFeature;
            }
            try { return model.Parameter(fullName) as IDimension; }
            catch { }
            return null;
        }

        private static string[] Strings(object raw)
        {
            Array array = raw as Array;
            if (array == null) return new string[0];
            var result = new string[array.Length];
            for (int index = 0; index < array.Length; index++)
                result[index] = Convert.ToString(array.GetValue(index),
                    CultureInfo.InvariantCulture) ?? "";
            return result;
        }

        private static double[] Doubles(object raw)
        {
            Array array = raw as Array;
            if (array == null) return new double[0];
            var result = new double[array.Length];
            for (int index = 0; index < array.Length; index++)
                result[index] = Convert.ToDouble(array.GetValue(index),
                    CultureInfo.InvariantCulture);
            return result;
        }

        private static IEnumerable<DisplayInView> Enumerate(IDrawingDoc drawing)
        {
            object viewObject = drawing.GetFirstView();
            int guard = 0;
            while (viewObject != null && guard++ < 2000)
            {
                IView view = viewObject as IView;
                viewObject = view != null ? view.GetNextView() : null;
                if (view == null) continue;
                object displayObject = view.GetFirstDisplayDimension5();
                int displayGuard = 0;
                while (displayObject != null && displayGuard++ < 2000)
                {
                    IDisplayDimension display = displayObject as IDisplayDimension;
                    displayObject = display != null ? display.GetNext5() : null;
                    if (display != null) yield return new DisplayInView { View = view, Display = display };
                }
            }
        }

        private static IView FindView(IDrawingDoc drawing, string name) =>
            EnumerateViews(drawing).SingleOrDefault(item => item.Name == name);
        private static IEnumerable<IView> EnumerateViews(IDrawingDoc drawing)
        {
            object value = drawing.GetFirstView(); int guard = 0;
            while (value != null && guard++ < 2000)
            { IView view = value as IView; value = view != null ? view.GetNextView() : null;
                if (view != null) yield return view; }
        }
        private static string SelectionName(IDisplayDimension display)
        { try { return display.GetNameForSelection() ?? ""; } catch { return ""; } }
        private static string FullName(IDisplayDimension display)
        { try { var d = display.GetDimension2(0) as IDimension; return d != null ? d.FullName ?? "" : ""; }
            catch { return ""; } }
        private static bool IsFinite(double value) =>
            !double.IsNaN(value) && !double.IsInfinity(value);
        private static bool IsAutomaticDiameterModifier(int nativeType, string prefix) =>
            prefix == "<MOD-DIAM>" &&
            (nativeType == (int)swDimensionType_e.swDiameterDimension ||
             nativeType == (int)swDimensionType_e.swDiametricLinearDimension);
        private static string JsonString(string value) =>
            Newtonsoft.Json.JsonConvert.SerializeObject(value ?? "");
        private static byte[] ToBytes(object raw)
        {
            byte[] bytes = raw as byte[];
            Array array = raw as Array;
            if (bytes != null || array == null) return bytes;
            bytes = new byte[array.Length];
            for (int index = 0; index < array.Length; index++)
                bytes[index] = Convert.ToByte(array.GetValue(index), CultureInfo.InvariantCulture);
            return bytes;
        }
        private static NativeToleranceRecord ReadTolerance(IDimension dimension)
        {
            IDimensionTolerance tolerance = dimension != null
                ? dimension.Tolerance as IDimensionTolerance : null;
            if (tolerance == null) return null;
            double minimum = 0, maximum = 0;
            return new NativeToleranceRecord
            {
                Type = tolerance.Type,
                MinimumValid = tolerance.GetMinValue2(out minimum) == 0,
                MaximumValid = tolerance.GetMaxValue2(out maximum) == 0,
                Minimum = minimum, Maximum = maximum,
                HoleFit = tolerance.GetHoleFitValue() ?? "",
                ShaftFit = tolerance.GetShaftFitValue() ?? ""
            };
        }
        private static NativeToleranceRecord ReadToleranceSafe(IDimension dimension)
        { try { return ReadTolerance(dimension); } catch (COMException) { return null; } }
        private static JArray ReadHoleCalloutVariables(IDisplayDimension display)
        {
            if (display == null || !display.IsHoleCallout()) return new JArray();
            object raw = display.GetHoleCalloutVariables();
            Array array = raw as Array;
            var result = new JArray();
            if (array == null) return result;
            foreach (object item in array)
            {
                ICalloutVariable variable = item as ICalloutVariable;
                if (variable == null)
                    throw new InvalidOperationException(
                        "Hole callout returned an unknown native variable object.");
                JToken value = JValue.CreateNull();
                JToken precision = JValue.CreateNull();
                JToken tolerancePrecision = JValue.CreateNull();
                string valueKind = "none";
                ICalloutLengthVariable length = item as ICalloutLengthVariable;
                ICalloutAngleVariable angle = item as ICalloutAngleVariable;
                ICalloutStringVariable text = item as ICalloutStringVariable;
                if (length != null)
                {
                    valueKind = "length"; value = new JValue(length.Length);
                    precision = new JValue(length.Precision);
                    tolerancePrecision = new JValue(length.TolerancePrecision);
                }
                else if (angle != null)
                {
                    valueKind = "angle"; value = new JValue(angle.Angle);
                    precision = new JValue(angle.Precision);
                }
                else if (text != null)
                {
                    valueKind = "string"; value = new JValue(text.String ?? "");
                }
                result.Add(new JObject
                {
                    ["variable_name"] = variable.VariableName ?? "",
                    ["user_readable_name"] = variable.UserReadableVariableName ?? "",
                    ["type"] = variable.Type, ["variable_type"] = variable.VariableType,
                    ["tolerance_type"] = variable.ToleranceType,
                    ["tolerance_minimum_si"] = variable.ToleranceMin,
                    ["tolerance_maximum_si"] = variable.ToleranceMax,
                    ["show_parentheses"] = variable.ShowParenthesis,
                    ["hole_fit"] = variable.HoleFit ?? "",
                    ["shaft_fit"] = variable.ShaftFit ?? "", ["fit_type"] = variable.FitType,
                    ["value_kind"] = valueKind, ["value"] = value,
                    ["precision"] = precision, ["tolerance_precision"] = tolerancePrecision
                });
            }
            return result;
        }
        private static bool Fail(string code, string id, string message,
            out DimensionPlanNativeError error)
        { error = new DimensionPlanNativeError { Code = code, DimensionId = id, Message = message };
            return false; }

        private sealed class DisplayInView { public IView View; public IDisplayDimension Display; }
        private sealed class ViewDimensionAggregate
        { public string DimensionId; public double ValueSi; }
        private sealed class NativeDimensionRecord
        {
            public string ViewName, SelectionName, FullName, AggregateDimensionId,
                Prefix, Suffix, AllText;
            public int Type, Precision, DrivenState, Unit;
            public bool IsHoleCallout, UseDocumentUnits, ShowParentheses, DisplayAsChain;
            public double ValueSi, PositionX, PositionY;
            public List<string> ModelPersistentReferences;
            public NativeToleranceRecord Tolerance;
            public JArray HoleCalloutVariables;
            public string PersistenceFingerprint() => new JObject
            {
                ["text"] = AllText, ["hole_callout_variables"] = HoleCalloutVariables.DeepClone(),
                ["tolerance"] = Tolerance != null ? Tolerance.ToJson() : JValue.CreateNull()
            }.ToString(Newtonsoft.Json.Formatting.None);
            public JObject ToJson(string dimensionId) => new JObject
            {
                ["dimension_id"] = dimensionId, ["view"] = ViewName,
                ["selection_name"] = SelectionName, ["full_name"] = FullName,
                ["native_type"] = Type, ["is_hole_callout"] = IsHoleCallout,
                ["value_si"] = double.IsNaN(ValueSi) ? JValue.CreateNull() : (JToken)ValueSi,
                ["position_sheet_m"] = new JArray(PositionX, PositionY),
                ["prefix"] = Prefix, ["suffix"] = Suffix,
                ["text"] = AllText,
                ["precision"] = Precision, ["driven_state"] = DrivenState,
                ["use_document_units"] = UseDocumentUnits, ["unit"] = Unit,
                ["show_parentheses"] = ShowParentheses,
                ["display_as_chain"] = DisplayAsChain,
                ["hole_callout_variables"] = HoleCalloutVariables.DeepClone(),
                ["tolerance"] = Tolerance != null ? Tolerance.ToJson() : JValue.CreateNull(),
                ["model_persistent_references"] = new JArray(ModelPersistentReferences.OrderBy(x => x))
            };
        }
        private sealed class NativeToleranceRecord
        {
            public int Type; public bool MinimumValid, MaximumValid;
            public double Minimum, Maximum; public string HoleFit, ShaftFit;
            public JObject ToJson() => new JObject
            {
                ["type"] = Type, ["minimum_valid"] = MinimumValid,
                ["maximum_valid"] = MaximumValid, ["minimum_si"] = Minimum,
                ["maximum_si"] = Maximum, ["hole_fit"] = HoleFit,
                ["shaft_fit"] = ShaftFit
            };
        }
    }

    internal sealed class DimensionPlanNativeResult
    { public int BaselineCount; public Dictionary<string, string> Handles;
        public Dictionary<string, string> PersistenceFingerprints;
        public JObject InMemoryVerification; }
    internal sealed class DimensionPlanNativeError
    { public string Code, DimensionId, Message; }
}
