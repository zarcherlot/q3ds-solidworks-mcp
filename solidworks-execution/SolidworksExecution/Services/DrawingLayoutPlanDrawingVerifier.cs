using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>G5 independent read-only verifier for one committed final layout drawing.</summary>
    internal sealed class DrawingLayoutPlanDrawingVerifier
    {
        private readonly ISldWorks _solidWorks;
        private readonly DrawingLayoutPlanNativeExecutor _layout =
            new DrawingLayoutPlanNativeExecutor();
        private readonly DimensionPlanNativeExecutor _dimensions =
            new DimensionPlanNativeExecutor();

        public DrawingLayoutPlanDrawingVerifier(ISldWorks solidWorks)
        { _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks"); }

        public bool TryVerify(DrawingLayoutExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, string dimensionPlanSchemaPath,
            string verificationSchemaPath, out JObject result,
            out DrawingLayoutTransactionError error)
        {
            result = new JObject { ["verified"] = false }; error = null;
            DrawingLayoutVerificationInputs inputs; DrawingLayoutPlanContractError preflight;
            if (!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                planSha256, requestedOutputPath, dimensionPlanSchemaPath,
                verificationSchemaPath, out inputs, out preflight))
                return Fail(preflight.Code, preflight.JsonPointer, preflight.Message,
                    result, out error);
            string previousTitle = ActiveTitle();
            bool sourceWasOpen = false;
            IModelDoc2 drawingModel = null;
            try
            {
                if (_solidWorks.GetOpenDocumentByName(inputs.OutputPath) != null)
                    return Fail("DRAWING_LAYOUT_DRAWING_ALREADY_OPEN", "/output_path",
                        "Independent G5 verification requires the final drawing to be closed.",
                        result, out error);
                IModelDoc2 existingSource = _solidWorks.GetOpenDocumentByName(
                    inputs.DimensionPlan.SourceModel.Path) as IModelDoc2;
                sourceWasOpen = existingSource != null;
                if (existingSource != null && existingSource.GetSaveFlag())
                    return Fail("DRAWING_LAYOUT_SOURCE_MODEL_DIRTY", "/output_path",
                        "The referenced source model has unsaved changes.", result, out error);

                int openErrors = 0, openWarnings = 0;
                drawingModel = _solidWorks.OpenDoc6(inputs.OutputPath,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    "", ref openErrors, ref openWarnings) as IModelDoc2;
                IDrawingDoc drawing = drawingModel as IDrawingDoc;
                if (drawing == null || !drawingModel.IsOpenedReadOnly() ||
                    !PathEquals(drawingModel.GetPathName(), inputs.OutputPath))
                    return Fail("DRAWING_LAYOUT_READONLY_OPEN_FAILED", "/output_path",
                        "Independent read-only open failed (errors=" + openErrors +
                        ", warnings=" + openWarnings + ").", result, out error);
                IModelDoc2 loadedSource = _solidWorks.GetOpenDocumentByName(
                    inputs.DimensionPlan.SourceModel.Path) as IModelDoc2;
                if (loadedSource != null && loadedSource.GetSaveFlag())
                    return Fail("DRAWING_LAYOUT_SOURCE_MODEL_DIRTY", "/output_path",
                        "The source model became dirty during independent verification open.",
                        result, out error);
                drawingModel.ForceRebuild3(false); drawingModel.GraphicsRedraw2();

                JObject dimensionVerification; DimensionPlanNativeError dimensionError;
                if (!_dimensions.TryVerifyPersisted(drawingModel, drawing,
                    inputs.DimensionPlan, inputs.DimensionBaselineCount,
                    inputs.DimensionHandles, inputs.DimensionFingerprints,
                    out dimensionVerification, out dimensionError))
                    return Fail(dimensionError.Code, dimensionError.DimensionId,
                        dimensionError.Message, result, out error);

                JObject layoutVerification; DrawingLayoutNativeError layoutError;
                if (!_layout.TryVerifyPersisted(drawingModel, drawing, plan,
                    inputs.ExpectedDimensionSemantics, inputs.ExpectedViewSemantics,
                    inputs.ExpectedLayoutFingerprint, out layoutVerification,
                    out layoutError))
                    return Fail(layoutError.Code, layoutError.JsonPointer,
                        layoutError.Message, result, out error);
                if (!ValidateCompleteIdentity(plan, layoutVerification, out layoutError))
                    return Fail(layoutError.Code, layoutError.JsonPointer,
                        layoutError.Message, result, out error);

                Close(ref drawingModel);
                if (_solidWorks.GetOpenDocumentByName(inputs.OutputPath) != null)
                    return Fail("DRAWING_LAYOUT_VERIFICATION_CLOSE_FAILED", "/output_path",
                        "The independently verified final drawing remained open.", result,
                        out error);
                string finalHash = DrawingLayoutPlanContractValidator.FileSha256(
                    inputs.OutputPath);
                if (!String.Equals(finalHash, inputs.ArtifactSha256,
                    StringComparison.OrdinalIgnoreCase))
                    return Fail("DRAWING_LAYOUT_OUTPUT_CHANGED_DURING_VERIFICATION",
                        "/output_path", "Final drawing hash changed during read-only verification.",
                        result, out error);
                DrawingLayoutVerificationInputs after;
                if (!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                    planSha256, requestedOutputPath, dimensionPlanSchemaPath,
                    verificationSchemaPath, out after, out preflight))
                    return Fail(preflight.Code, preflight.JsonPointer, preflight.Message,
                        result, out error);
                result = new JObject
                {
                    ["verified"] = true, ["independent_read_only_reopen"] = true,
                    ["output_path"] = inputs.OutputPath,
                    ["verification_report"] = inputs.ReportPath,
                    ["artifact_sha256"] = finalHash,
                    ["plan_canonical_sha256"] = plan.PlanSha256,
                    ["layout_fingerprint_sha256"] = inputs.ExpectedLayoutFingerprint,
                    ["verified_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture),
                    ["dimension_verification"] = dimensionVerification,
                    ["layout_verification"] = layoutVerification,
                    ["complete_object_identity"] = true,
                    ["source_and_output_hashes_unchanged"] = true
                };
                return true;
            }
            catch (Exception ex)
            { return Fail("DRAWING_LAYOUT_INDEPENDENT_VERIFICATION_FAILED", "/output_path",
                ex.Message, result, out error); }
            finally
            {
                Close(ref drawingModel);
                if (!sourceWasOpen && inputs != null && inputs.DimensionPlan != null &&
                    inputs.DimensionPlan.SourceModel != null)
                {
                    IModelDoc2 source = _solidWorks.GetOpenDocumentByName(
                        inputs.DimensionPlan.SourceModel.Path) as IModelDoc2;
                    Close(ref source);
                }
                Restore(previousTitle);
            }
        }

        private static bool ValidateCompleteIdentity(DrawingLayoutExecutionPlan plan,
            JObject verification, out DrawingLayoutNativeError error)
        {
            error = null;
            JArray objects = verification.SelectToken("snapshot.objects") as JArray;
            JArray views = verification.SelectToken("snapshot.views") as JArray;
            if (objects == null || views == null)
                return IdentityFail("DRAWING_LAYOUT_INDEPENDENT_SNAPSHOT_INVALID", "",
                    "Independent snapshot has no objects or views.", out error);
            var expectedSources = new HashSet<string>(((JArray)plan.HandoffValue["objects"])
                .OfType<JObject>().Select(row => row.Value<string>("source_id")),
                StringComparer.Ordinal);
            var actualSources = new HashSet<string>(objects.OfType<JObject>().Select(row =>
                row.Value<string>("id")), StringComparer.Ordinal);
            if (expectedSources.Count == 0 || !expectedSources.SetEquals(actualSources))
                return IdentityFail("DRAWING_LAYOUT_OBJECT_IDENTITY_MISMATCH", "",
                    "Final drawing has missing, dangling or unplanned layout objects.", out error);
            var actualViews = new HashSet<string>(views.OfType<JObject>().Select(row =>
                row.Value<string>("name")), StringComparer.Ordinal);
            if (!plan.ViewNames.SetEquals(actualViews))
                return IdentityFail("DRAWING_LAYOUT_VIEW_IDENTITY_MISMATCH", "",
                    "Final drawing view inventory differs from the frozen plan.", out error);
            foreach (string leader in expectedSources.Where(id => id != null &&
                id.StartsWith("leader:", StringComparison.Ordinal)))
            {
                string owner = leader.Substring("leader:".Length);
                int last = owner.LastIndexOf(':');
                if (last > 0) owner = owner.Substring(0, last);
                if (!expectedSources.Contains(owner))
                    return IdentityFail("DRAWING_LAYOUT_DANGLING_LEADER", leader,
                        "A leader has no frozen owning annotation.", out error);
            }
            return true;
        }
        private string ActiveTitle()
        { try { IModelDoc2 active = _solidWorks.IActiveDoc2 as IModelDoc2;
            return active != null ? active.GetTitle() : null; } catch { return null; } }
        private void Restore(string title)
        { if (String.IsNullOrEmpty(title)) return; try { int errors = 0;
            _solidWorks.ActivateDoc3(title, false, 0, ref errors); } catch { } }
        private void Close(ref IModelDoc2 document)
        { if (document == null) return; try { _solidWorks.CloseDoc(document.GetTitle()); }
            catch { } document = null; }
        private static bool PathEquals(string first, string second)
        { try { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
            StringComparison.OrdinalIgnoreCase); } catch { return false; } }
        private static bool IdentityFail(string code, string pointer, string message,
            out DrawingLayoutNativeError error)
        { error = new DrawingLayoutNativeError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
        private static bool Fail(string code, string pointer, string message, JObject result,
            out DrawingLayoutTransactionError error)
        { error = new DrawingLayoutTransactionError { Code = code, JsonPointer = pointer,
            Message = message }; result["verified"] = false; result["error_code"] = code;
            result["json_pointer"] = pointer; return false; }
    }
}
