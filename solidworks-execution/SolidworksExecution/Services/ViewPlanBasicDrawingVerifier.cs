using System;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// B4 independent read-only verification transaction. It consumes only a complete compiled
    /// ViewPlan plus the committed output path; audit parsing, frozen-input hashing, and artifact
    /// hashing happen before COM. No drawing or model is saved by this verifier.
    /// </summary>
    internal sealed class ViewPlanBasicDrawingVerifier
    {
        private readonly ISldWorks _solidWorks;
        private readonly ViewPlanBasicViewExecutor _viewExecutor;

        public ViewPlanBasicDrawingVerifier(ISldWorks solidWorks)
        {
            _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks");
            _viewExecutor = new ViewPlanBasicViewExecutor(solidWorks);
        }

        public bool TryVerify(ViewPlanBasicExecutionPlan plan, string requestedOutputPath,
            out JObject result, out ViewPlanBasicDrawingVerificationError error)
        {
            result = new JObject { ["verified"] = false };
            error = null;
            ViewPlanBasicVerificationInputs inputs;
            ViewPlanExecutionContractError preflightError;
            if (!new ViewPlanBasicVerificationPreflight().TryValidate(plan,
                requestedOutputPath, out inputs, out preflightError))
                return Fail(preflightError.Code, preflightError.JsonPointer,
                    preflightError.Message, result, out error);

            string previousTitle = GetActiveDocumentTitle();
            IModelDoc2 drawingModel = null;
            bool sourceWasOpen = false;
            try
            {
                if (_solidWorks.GetOpenDocumentByName(inputs.OutputPath) != null)
                    return Fail("VIEW_PLAN_DRAWING_ALREADY_OPEN", "/output_path",
                        "Independent verification requires the output drawing to be closed.",
                        result, out error);
                var existingSource = _solidWorks.GetOpenDocumentByName(plan.ModelPath) as IModelDoc2;
                sourceWasOpen = existingSource != null;
                if (existingSource != null && existingSource.GetSaveFlag())
                    return Fail("VIEW_PLAN_MODEL_HAS_UNSAVED_CHANGES", "/model_path",
                        "The source model has unsaved changes; verification is refused.",
                        result, out error);

                int openErrors = 0;
                int openWarnings = 0;
                drawingModel = _solidWorks.OpenDoc6(inputs.OutputPath, 3, 3, "",
                    ref openErrors, ref openWarnings) as IModelDoc2;
                var drawing = drawingModel as IDrawingDoc;
                if (drawing == null || !drawingModel.IsOpenedReadOnly() ||
                    !PathEquals(drawingModel.GetPathName(), inputs.OutputPath))
                    return Fail("VIEW_PLAN_DRAWING_REOPEN_FAILED", "/output_path",
                        "Independent read-only reopen failed (errors=" + openErrors +
                        ", warnings=" + openWarnings + ").", result, out error);

                var loadedSource = _solidWorks.GetOpenDocumentByName(plan.ModelPath) as IModelDoc2;
                if (loadedSource != null && loadedSource.GetSaveFlag())
                    return Fail("VIEW_PLAN_MODEL_HAS_UNSAVED_CHANGES", "/model_path",
                        "The source model became dirty during verification open.", result,
                        out error);
                drawingModel.ForceRebuild3(false);
                drawingModel.GraphicsRedraw2();

                JObject snapshot;
                ViewPlanBasicViewExecutionError verificationError;
                if (!_viewExecutor.TryVerifyPersisted(drawing, plan, inputs.ExpectedHandles,
                    null, null, null, null, out snapshot, out verificationError))
                    return Fail(verificationError.Code,
                        string.IsNullOrEmpty(verificationError.ViewId) ? "/output_path" :
                            "/views/" + verificationError.ViewId,
                        verificationError.Message, result, out error);

                string title = drawingModel.GetTitle();
                _solidWorks.CloseDoc(title);
                drawingModel = null;
                if (_solidWorks.GetOpenDocumentByName(inputs.OutputPath) != null)
                    return Fail("VIEW_PLAN_VERIFICATION_CLOSE_FAILED", "/output_path",
                        "The independently verified drawing remained open after CloseDoc.",
                        result, out error);
                string finalSha256 = ComputeFileSha256(inputs.OutputPath);
                if (!string.Equals(finalSha256, inputs.ArtifactSha256,
                    StringComparison.OrdinalIgnoreCase))
                    return Fail("VIEW_PLAN_OUTPUT_CHANGED_DURING_VERIFICATION", "/output_path",
                        "The drawing SHA-256 changed during read-only verification.", result,
                        out error);

                result = new JObject
                {
                    ["verified"] = true,
                    ["independent_read_only_reopen"] = true,
                    ["output_path"] = inputs.OutputPath,
                    ["verification_report"] = inputs.ReportPath,
                    ["artifact_sha256"] = finalSha256,
                    ["plan_canonical_sha256"] = plan.PlanCanonicalSha256,
                    ["verified_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture),
                    ["verification"] = snapshot
                };
                return true;
            }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_INDEPENDENT_VERIFICATION_FAILED", "/output_path",
                    ex.Message, result, out error);
            }
            finally
            {
                try
                {
                    if (drawingModel != null) _solidWorks.CloseDoc(drawingModel.GetTitle());
                }
                catch { }
                try
                {
                    if (!sourceWasOpen)
                    {
                        var source = _solidWorks.GetOpenDocumentByName(plan.ModelPath) as IModelDoc2;
                        if (source != null) _solidWorks.CloseDoc(source.GetTitle());
                    }
                }
                catch { }
                RestoreActiveDocument(previousTitle);
            }
        }

        private string GetActiveDocumentTitle()
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
                _solidWorks.ActivateDoc3(title, false, 0, ref errors);
            }
            catch { }
        }

        private static string ComputeFileSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
                FileShare.Read))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "")
                    .ToLowerInvariant();
        }

        private static bool PathEquals(string first, string second)
        {
            try
            {
                return string.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }

        private static bool Fail(string code, string pointer, string message, JObject result,
            out ViewPlanBasicDrawingVerificationError error)
        {
            error = new ViewPlanBasicDrawingVerificationError
            {
                Code = code,
                JsonPointer = pointer,
                Message = message
            };
            if (result != null)
            {
                result["verified"] = false;
                result["error_code"] = code;
                result["json_pointer"] = pointer;
            }
            return false;
        }
    }

    internal sealed class ViewPlanBasicDrawingVerificationError
    {
        public string Code { get; set; }
        public string JsonPointer { get; set; }
        public string Message { get; set; }
    }
}
