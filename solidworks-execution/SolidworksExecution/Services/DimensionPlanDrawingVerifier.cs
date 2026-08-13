using System;
using System.Globalization;
using System.IO;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>F6 independent read-only verifier for one committed dimension drawing.</summary>
    internal sealed class DimensionPlanDrawingVerifier
    {
        private readonly ISldWorks _solidWorks;
        private readonly DimensionPlanNativeExecutor _executor =
            new DimensionPlanNativeExecutor();

        public DimensionPlanDrawingVerifier(ISldWorks solidWorks)
        { _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks"); }

        public bool TryVerify(DimensionPlanExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, out JObject result,
            out DimensionPlanTransactionError error)
        {
            result = new JObject { ["verified"] = false }; error = null;
            DimensionPlanVerificationInputs inputs; DimensionPlanContractError preflight;
            if (!new DimensionPlanVerificationPreflight().TryValidate(plan, planPath,
                planSha256, requestedOutputPath, out inputs, out preflight))
                return Fail(preflight.Code, preflight.JsonPointer, preflight.Message,
                    result, out error);
            string previousTitle = ActiveTitle();
            bool sourceWasOpen = false;
            IModelDoc2 drawingModel = null;
            try
            {
                if (_solidWorks.GetOpenDocumentByName(inputs.OutputPath) != null)
                    return Fail("DIMENSION_DRAWING_ALREADY_OPEN", "/output_path",
                        "Independent verification requires the output drawing to be closed.",
                        result, out error);
                IModelDoc2 existingSource = _solidWorks.GetOpenDocumentByName(
                    plan.SourceModel.Path) as IModelDoc2;
                sourceWasOpen = existingSource != null;
                if (existingSource != null && existingSource.GetSaveFlag())
                    return Fail("DIMENSION_SOURCE_MODEL_DIRTY", "/output_path",
                        "The source model has unsaved changes.", result, out error);

                int openErrors = 0, openWarnings = 0;
                drawingModel = _solidWorks.OpenDoc6(inputs.OutputPath,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    "", ref openErrors, ref openWarnings) as IModelDoc2;
                IDrawingDoc drawing = drawingModel as IDrawingDoc;
                if (drawing == null || !drawingModel.IsOpenedReadOnly() ||
                    !PathEquals(drawingModel.GetPathName(), inputs.OutputPath))
                    return Fail("DIMENSION_DRAWING_REOPEN_FAILED", "/output_path",
                        "Independent read-only reopen failed (errors=" + openErrors +
                        ", warnings=" + openWarnings + ").", result, out error);
                IModelDoc2 loadedSource = _solidWorks.GetOpenDocumentByName(
                    plan.SourceModel.Path) as IModelDoc2;
                if (loadedSource != null && loadedSource.GetSaveFlag())
                    return Fail("DIMENSION_SOURCE_MODEL_DIRTY", "/output_path",
                        "The source model became dirty during verification open.", result,
                        out error);
                drawingModel.ForceRebuild3(false);
                drawingModel.GraphicsRedraw2();
                JObject snapshot; DimensionPlanNativeError nativeError;
                if (!_executor.TryVerifyPersisted(drawingModel, drawing, plan,
                    inputs.BaselineCount, inputs.ExpectedHandles,
                    inputs.ExpectedFingerprints, out snapshot, out nativeError))
                    return Fail(nativeError.Code, nativeError.DimensionId,
                        nativeError.Message, result, out error);
                Close(ref drawingModel);
                if (_solidWorks.GetOpenDocumentByName(inputs.OutputPath) != null)
                    return Fail("DIMENSION_VERIFICATION_CLOSE_FAILED", "/output_path",
                        "The independently verified drawing remained open.", result, out error);
                string finalHash = DimensionPlanContractValidator.FileSha256(inputs.OutputPath);
                if (!String.Equals(finalHash, inputs.ArtifactSha256,
                    StringComparison.OrdinalIgnoreCase))
                    return Fail("DIMENSION_OUTPUT_CHANGED_DURING_VERIFICATION", "/output_path",
                        "The drawing hash changed during read-only verification.", result,
                        out error);
                DimensionPlanVerificationInputs after;
                if (!new DimensionPlanVerificationPreflight().TryValidate(plan, planPath,
                    planSha256, requestedOutputPath, out after, out preflight))
                    return Fail(preflight.Code, preflight.JsonPointer, preflight.Message,
                        result, out error);
                result = new JObject
                {
                    ["verified"] = true, ["independent_read_only_reopen"] = true,
                    ["output_path"] = inputs.OutputPath,
                    ["verification_report"] = inputs.ReportPath,
                    ["artifact_sha256"] = finalHash,
                    ["plan_canonical_sha256"] = plan.PlanSha256,
                    ["verified_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture), ["verification"] = snapshot
                };
                return true;
            }
            catch (Exception ex)
            { return Fail("DIMENSION_INDEPENDENT_VERIFICATION_FAILED", "/output_path",
                ex.Message, result, out error); }
            finally
            {
                Close(ref drawingModel);
                if (!sourceWasOpen)
                {
                    IModelDoc2 source = _solidWorks.GetOpenDocumentByName(
                        plan.SourceModel.Path) as IModelDoc2;
                    Close(ref source);
                }
                Restore(previousTitle);
            }
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
        private static bool Fail(string code, string pointer, string message, JObject result,
            out DimensionPlanTransactionError error)
        { error = new DimensionPlanTransactionError { Code = code, JsonPointer = pointer,
            Message = message }; result["verified"] = false; result["error_code"] = code;
            result["json_pointer"] = pointer; return false; }
    }
}
