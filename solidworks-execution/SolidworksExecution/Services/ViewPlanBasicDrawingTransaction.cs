using System;
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
    /// B3 disk transaction for the executable ViewPlan basic-view subset. Inputs are hash-bound
    /// before COM, the ready drawing is copied through SolidWorks CopyDocument to a same-directory
    /// temporary artifact, and only a save/close/read-only-reopen verified result is committed. The
    /// source drawing is never opened for write and neither final path may pre-exist.
    /// </summary>
    internal sealed class ViewPlanBasicDrawingTransaction
    {
        private readonly ISldWorks _solidWorks;
        private readonly ViewPlanBasicViewExecutor _viewExecutor;

        public ViewPlanBasicDrawingTransaction(ISldWorks solidWorks)
        {
            _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks");
            _viewExecutor = new ViewPlanBasicViewExecutor(solidWorks);
        }

        public bool TryExecute(ViewPlanBasicExecutionPlan plan, string requestedOutputPath,
            string operationId, out JObject result, out ViewPlanBasicDrawingTransactionError error)
        {
            result = new JObject { ["committed"] = false };
            error = null;
            ViewPlanBasicTransactionPaths transactionPaths;
            ViewPlanExecutionContractError preflightError;
            if (!new ViewPlanBasicTransactionPreflight().TryValidate(plan, requestedOutputPath,
                out transactionPaths, out preflightError))
            {
                error = new ViewPlanBasicDrawingTransactionError
                {
                    Code = preflightError.Code,
                    JsonPointer = preflightError.JsonPointer,
                    Message = preflightError.Message
                };
                result["error_code"] = preflightError.Code;
                result["json_pointer"] = preflightError.JsonPointer;
                return false;
            }
            string outputPath = transactionPaths.OutputPath;
            string reportPath = transactionPaths.ReportPath;

            string directory = Path.GetDirectoryName(outputPath);
            string fileName = Path.GetFileNameWithoutExtension(outputPath);
            string nonce = Guid.NewGuid().ToString("N");
            string temporaryDrawing = Path.Combine(directory,
                fileName + ".q3ds-vp-" + nonce + ".SLDDRW");
            string temporaryReport = Path.Combine(directory,
                "." + fileName + ".q3ds-vp-" + nonce + ".tmp.verification.json");

            string previousTitle = GetActiveDocumentTitle();
            IModelDoc2 sourceModel = null;
            IModelDoc2 drawingModel = null;
            IModelDoc2 reopenedModel = null;
            bool sourceOwned = false;
            bool outputMoved = false;
            bool reportMoved = false;
            bool completed = false;
            try
            {
                string initializerSha256Before = ComputeFileSha256(plan.DrawingPath);
                if (_solidWorks.IGetFirstDocument2() != null)
                {
                    result["open_documents"] = BuildOpenDocumentList();
                    return Fail("VIEW_PLAN_COPY_REQUIRES_NO_OPEN_DOCUMENTS",
                        "SolidWorks CopyDocument requires all documents to be closed; refusing " +
                        "to close or alter the existing user session.", result, out error);
                }
                int copyResult = _solidWorks.CopyDocument(plan.DrawingPath, temporaryDrawing,
                    null, null, 0);
                string initializerSha256After = ComputeFileSha256(plan.DrawingPath);
                var initializerCopy = new JObject
                {
                    ["strategy"] = "solidworks_copy_document",
                    ["source_sha256_before"] = initializerSha256Before,
                    ["source_sha256_after"] = initializerSha256After,
                    ["copy_result"] = copyResult,
                    ["temporary_exists"] = File.Exists(temporaryDrawing)
                };
                result["initializer_copy"] = initializerCopy;
                if (copyResult != (int)swMoveCopyError_e.swMoveCopyErrorNone ||
                    !File.Exists(temporaryDrawing))
                    return Fail("VIEW_PLAN_DRAWING_COPY_FAILED",
                        "SolidWorks CopyDocument failed (result=" + copyResult + ").",
                        result, out error);
                if (!string.Equals(initializerSha256Before, initializerSha256After,
                    StringComparison.OrdinalIgnoreCase))
                    return Fail("VIEW_PLAN_INITIALIZER_MUTATED",
                        "The immutable initializer drawing changed during CopyDocument.",
                        result, out error);

                sourceModel = _solidWorks.GetOpenDocumentByName(plan.ModelPath) as IModelDoc2;
                sourceOwned = sourceModel == null;
                if (sourceOwned)
                {
                    int modelErrors = 0;
                    int modelWarnings = 0;
                    sourceModel = _solidWorks.OpenDoc6(plan.ModelPath, 1, 3, "",
                        ref modelErrors, ref modelWarnings) as IModelDoc2;
                    if (sourceModel == null)
                        return Fail("VIEW_PLAN_MODEL_OPEN_FAILED",
                            "Read-only model open failed (errors=" + modelErrors +
                            ", warnings=" + modelWarnings + ").", result, out error);
                }

                int drawingErrors = 0;
                int drawingWarnings = 0;
                drawingModel = _solidWorks.OpenDoc6(temporaryDrawing, 3, 1, "",
                    ref drawingErrors, ref drawingWarnings) as IModelDoc2;
                var drawing = drawingModel as IDrawingDoc;
                if (drawing == null || drawingModel.IsOpenedReadOnly())
                    return Fail("VIEW_PLAN_DRAWING_OPEN_FAILED",
                        "Writable transaction drawing open failed (errors=" + drawingErrors +
                        ", warnings=" + drawingWarnings + ").", result, out error);

                ViewPlanBasicViewExecutionResult creation;
                ViewPlanBasicViewExecutionError creationError;
                if (!_viewExecutor.TryCreate(drawingModel, drawing, sourceModel, plan, sourceOwned,
                    out creation, out creationError))
                    return Fail(creationError.Code,
                        PrefixView(creationError.ViewId, creationError.Message), result, out error);
                result["in_memory_verification"] = creation.InMemoryReadback;

                drawingModel.ClearSelection2(true);
                bool rebuildResult = drawingModel.ForceRebuild3(false);
                drawingModel.GraphicsRedraw2();
                var saveDiagnostics = BuildSaveDiagnostics(
                    drawingModel, temporaryDrawing, rebuildResult);
                result["save_diagnostics"] = saveDiagnostics;
                int saveErrors = 0;
                int saveWarnings = 0;
                bool saved = drawingModel.Save3(1, ref saveErrors, ref saveWarnings);
                saveDiagnostics["save_returned"] = saved;
                saveDiagnostics["save_errors"] = saveErrors;
                saveDiagnostics["save_warnings"] = saveWarnings;
                saveDiagnostics["save_flag_after"] = SafeGetSaveFlag(drawingModel);
                saveDiagnostics["file_exists_after"] = File.Exists(temporaryDrawing);
                saveDiagnostics["file_length_after"] = SafeFileLength(temporaryDrawing);
                if (!saved || saveErrors != 0 || !File.Exists(temporaryDrawing))
                    return Fail("VIEW_PLAN_DRAWING_SAVE_FAILED",
                        "Save3 failed (errors=" + saveErrors + ", warnings=" + saveWarnings + ").",
                        result, out error);

                string drawingTitle = drawingModel.GetTitle();
                _solidWorks.CloseDoc(drawingTitle);
                drawingModel = null;
                if (_solidWorks.GetOpenDocumentByName(temporaryDrawing) != null)
                    return Fail("VIEW_PLAN_DRAWING_CLOSE_FAILED",
                        "Temporary drawing remained open after CloseDoc.", result, out error);

                int reopenErrors = 0;
                int reopenWarnings = 0;
                reopenedModel = _solidWorks.OpenDoc6(temporaryDrawing, 3, 3, "",
                    ref reopenErrors, ref reopenWarnings) as IModelDoc2;
                var reopenedDrawing = reopenedModel as IDrawingDoc;
                if (reopenedDrawing == null || !reopenedModel.IsOpenedReadOnly())
                    return Fail("VIEW_PLAN_DRAWING_REOPEN_FAILED",
                        "Read-only reopen failed (errors=" + reopenErrors +
                        ", warnings=" + reopenWarnings + ").", result, out error);
                reopenedModel.ForceRebuild3(false);
                reopenedModel.GraphicsRedraw2();

                JObject persistedSnapshot;
                ViewPlanBasicViewExecutionError verificationError;
                if (!_viewExecutor.TryVerifyPersisted(reopenedDrawing, plan,
                    creation.PersistentHandles, creation.SectionFingerprints,
                    creation.C2Fingerprints,
                    creation.AuxiliaryFingerprints,
                    creation.CenterElementFingerprints,
                    out persistedSnapshot, out verificationError))
                    return Fail(verificationError.Code,
                        PrefixView(verificationError.ViewId, verificationError.Message),
                        result, out error);
                result["reopen_verification"] = persistedSnapshot;

                string reopenedTitle = reopenedModel.GetTitle();
                _solidWorks.CloseDoc(reopenedTitle);
                reopenedModel = null;
                if (_solidWorks.GetOpenDocumentByName(temporaryDrawing) != null)
                    return Fail("VIEW_PLAN_VERIFICATION_CLOSE_FAILED",
                        "Read-only verification drawing remained open after CloseDoc.",
                        result, out error);

                string artifactSha256 = ComputeFileSha256(temporaryDrawing);
                var audit = new JObject
                {
                    ["schema_version"] = "1.0",
                    ["operation_id"] = operationId,
                    ["generated_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture),
                    ["plan_id"] = plan.PlanId,
                    ["plan_canonical_sha256"] = plan.PlanCanonicalSha256,
                    ["artifact_sha256"] = artifactSha256,
                    ["output_path"] = outputPath,
                    ["verified"] = true,
                    ["input_artifacts"] = new JArray(plan.InputArtifacts.Select(item =>
                        new JObject
                        {
                            ["role"] = item.Role,
                            ["path"] = item.Path,
                            ["sha256"] = item.Sha256
                        })),
                    ["view_handles"] = JObject.FromObject(creation.PersistentHandles),
                    ["verification"] = persistedSnapshot
                };
                File.WriteAllText(temporaryReport, audit.ToString(Formatting.Indented),
                    new UTF8Encoding(false));

                if (File.Exists(outputPath) || File.Exists(reportPath))
                    return Fail("VIEW_PLAN_OUTPUT_RACE",
                        "A final output path appeared during execution; commit was refused.",
                        result, out error);
                File.Move(temporaryDrawing, outputPath);
                outputMoved = true;
                File.Move(temporaryReport, reportPath);
                reportMoved = true;
                completed = true;
                result["committed"] = true;
                result["output_path"] = outputPath;
                result["verification_report"] = reportPath;
                result["artifact_sha256"] = artifactSha256;
                result["plan_canonical_sha256"] = plan.PlanCanonicalSha256;
                result["view_handles"] = JObject.FromObject(creation.PersistentHandles);
                return true;
            }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_DRAWING_TRANSACTION_FAILED", ex.Message, result, out error);
            }
            finally
            {
                try
                {
                    if (reopenedModel != null) _solidWorks.CloseDoc(reopenedModel.GetTitle());
                }
                catch { }
                try
                {
                    if (drawingModel != null) _solidWorks.CloseDoc(drawingModel.GetTitle());
                }
                catch { }
                try
                {
                    if (sourceOwned && sourceModel != null)
                        _solidWorks.CloseDoc(sourceModel.GetTitle());
                }
                catch { }
                RestoreActiveDocument(previousTitle);
                if (!completed)
                {
                    DeleteIfPresent(temporaryDrawing);
                    DeleteIfPresent(temporaryReport);
                    if (reportMoved) DeleteIfPresent(reportPath);
                    if (outputMoved) DeleteIfPresent(outputPath);
                }
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

        private JArray BuildOpenDocumentList()
        {
            var documents = new JArray();
            try
            {
                IModelDoc2 current = _solidWorks.IGetFirstDocument2();
                while (current != null)
                {
                    documents.Add(new JObject
                    {
                        ["title"] = SafeDocumentTitle(current),
                        ["path"] = SafeDocumentPath(current),
                        ["read_only"] = SafeIsOpenedReadOnly(current)
                    });
                    current = current.IGetNext();
                }
            }
            catch { }
            return documents;
        }

        private JObject BuildSaveDiagnostics(IModelDoc2 drawingModel,
            string temporaryDrawing, bool rebuildResult)
        {
            var diagnostics = new JObject
            {
                ["document_path"] = SafeDocumentPath(drawingModel),
                ["document_title"] = SafeDocumentTitle(drawingModel),
                ["opened_read_only"] = SafeIsOpenedReadOnly(drawingModel),
                ["save_flag_before"] = SafeGetSaveFlag(drawingModel),
                ["rebuild_result"] = rebuildResult,
                ["file_exists_before"] = File.Exists(temporaryDrawing),
                ["file_length_before"] = SafeFileLength(temporaryDrawing)
            };
            return diagnostics;
        }

        private static string SafeDocumentPath(IModelDoc2 model)
        {
            try { return model != null ? model.GetPathName() : null; }
            catch { return null; }
        }

        private static string SafeDocumentTitle(IModelDoc2 model)
        {
            try { return model != null ? model.GetTitle() : null; }
            catch { return null; }
        }

        private static bool? SafeIsOpenedReadOnly(IModelDoc2 model)
        {
            try { return model != null ? (bool?)model.IsOpenedReadOnly() : null; }
            catch { return null; }
        }

        private static bool? SafeGetSaveFlag(IModelDoc2 model)
        {
            try { return model != null ? (bool?)model.GetSaveFlag() : null; }
            catch { return null; }
        }

        private static long? SafeFileLength(string path)
        {
            try { return File.Exists(path) ? (long?)new FileInfo(path).Length : null; }
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

        private static string PrefixView(string viewId, string message)
        {
            return string.IsNullOrEmpty(viewId) ? message : "View '" + viewId + "': " + message;
        }

        private static void DeleteIfPresent(string path)
        {
            try { if (!string.IsNullOrEmpty(path) && File.Exists(path)) File.Delete(path); }
            catch { }
        }

        private static bool Fail(string code, string message, JObject result,
            out ViewPlanBasicDrawingTransactionError error)
        {
            error = new ViewPlanBasicDrawingTransactionError { Code = code, Message = message };
            if (result != null)
            {
                result["committed"] = false;
                result["error_code"] = code;
            }
            return false;
        }

    }

    internal sealed class ViewPlanBasicDrawingTransactionError
    {
        public string Code { get; set; }
        public string JsonPointer { get; set; }
        public string Message { get; set; }
    }
}
