using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// F4 no-overwrite transaction: CopyDocument, create, memory verify, save, close, read-only
    /// reopen verify, then commit a new drawing and its sidecar together or roll both back.
    /// </summary>
    internal sealed class DimensionPlanDrawingTransaction
    {
        private readonly ISldWorks _solidWorks;
        private readonly DimensionPlanNativeExecutor _executor = new DimensionPlanNativeExecutor();

        public DimensionPlanDrawingTransaction(ISldWorks solidWorks)
        { _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks"); }

        public bool TryExecute(DimensionPlanExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, string operationId,
            out JObject result, out DimensionPlanTransactionError error)
        {
            result = new JObject { ["committed"] = false }; error = null;
            DimensionPlanTransactionPaths paths; DimensionPlanContractError preflight;
            if (!new DimensionPlanTransactionPreflight().TryValidate(plan, planPath, planSha256,
                requestedOutputPath, out paths, out preflight))
                return Fail(preflight.Code, preflight.JsonPointer, preflight.Message, result, out error);

            string directory = Path.GetDirectoryName(paths.OutputPath);
            string stem = Path.GetFileNameWithoutExtension(paths.OutputPath);
            string nonce = Guid.NewGuid().ToString("N");
            string temporaryDrawing = Path.Combine(directory, stem + ".q3ds-dim-" + nonce + ".SLDDRW");
            string temporaryReport = Path.Combine(directory, "." + stem + ".q3ds-dim-" + nonce + ".tmp.json");
            IModelDoc2 sourceModel = null, drawingModel = null, reopenedModel = null;
            bool outputMoved = false, reportMoved = false, complete = false;
            string stage = "preflight";
            var before = FrozenHashes(plan, paths.PlanPath);
            try
            {
                if (_solidWorks.IGetFirstDocument2() != null)
                    return Fail("DIMENSION_COPY_REQUIRES_NO_OPEN_DOCUMENTS", "",
                        "CopyDocument requires a clean SolidWorks session; existing documents are never closed implicitly.",
                        result, out error);
                stage = "copy_source_drawing";
                int copy = _solidWorks.CopyDocument(plan.SourceDrawing.Path, temporaryDrawing,
                    null, null, 0);
                result["copy"] = new JObject { ["strategy"] = "solidworks_copy_document",
                    ["result"] = copy, ["temporary_exists"] = File.Exists(temporaryDrawing) };
                if (copy != (int)swMoveCopyError_e.swMoveCopyErrorNone || !File.Exists(temporaryDrawing))
                    return Fail("DIMENSION_DRAWING_COPY_FAILED", "",
                        "SolidWorks CopyDocument failed (result=" + copy + ").", result, out error);
                if (!FrozenHashesEqual(before, FrozenHashes(plan, paths.PlanPath)))
                    return Fail("DIMENSION_FROZEN_INPUT_MUTATED", "",
                        "A frozen input changed during CopyDocument.", result, out error);

                stage = "open_source_model";
                int errors = 0, warnings = 0;
                sourceModel = _solidWorks.OpenDoc6(plan.SourceModel.Path,
                    (int)swDocumentTypes_e.swDocPART,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    plan.Configuration, ref errors, ref warnings) as IModelDoc2;
                if (sourceModel == null || !sourceModel.IsOpenedReadOnly())
                    return Fail("DIMENSION_MODEL_OPEN_FAILED", "",
                        "The source model could not be opened read-only.", result, out error);
                bool sourceSaveFlagBefore = sourceModel.GetSaveFlag();

                stage = "open_transaction_drawing";
                errors = 0; warnings = 0;
                drawingModel = _solidWorks.OpenDoc6(temporaryDrawing,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                    "", ref errors, ref warnings) as IModelDoc2;
                IDrawingDoc drawing = drawingModel as IDrawingDoc;
                if (drawing == null || drawingModel.IsOpenedReadOnly())
                    return Fail("DIMENSION_DRAWING_OPEN_FAILED", "",
                        "The transaction drawing could not be opened writable.", result, out error);

                stage = "create_and_verify_in_memory";
                DimensionPlanNativeResult created; DimensionPlanNativeError nativeError;
                if (!_executor.TryCreate(drawingModel, drawing, sourceModel, plan,
                    out created, out nativeError))
                    return Fail(nativeError.Code, DimensionPointer(nativeError.DimensionId),
                        nativeError.Message, result, out error);
                result["in_memory_verification"] = created.InMemoryVerification;
                stage = "save_transaction_drawing";
                drawingModel.ClearSelection2(true);
                bool rebuilt = drawingModel.ForceRebuild3(false);
                drawingModel.GraphicsRedraw2();
                int saveErrors = 0, saveWarnings = 0;
                bool saved = drawingModel.Save3((int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                    ref saveErrors, ref saveWarnings);
                result["save"] = new JObject { ["rebuild"] = rebuilt, ["saved"] = saved,
                    ["errors"] = saveErrors, ["warnings"] = saveWarnings };
                if (!saved || saveErrors != 0 || !File.Exists(temporaryDrawing))
                    return Fail("DIMENSION_DRAWING_SAVE_FAILED", "",
                        "Save3 failed; the transaction will be rolled back.", result, out error);
                Close(ref drawingModel);
                if (_solidWorks.GetOpenDocumentByName(temporaryDrawing) != null)
                    return Fail("DIMENSION_DRAWING_CLOSE_FAILED", "",
                        "The transaction drawing remained open after CloseDoc.", result, out error);

                stage = "reopen_transaction_drawing_read_only";
                errors = 0; warnings = 0;
                reopenedModel = _solidWorks.OpenDoc6(temporaryDrawing,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    "", ref errors, ref warnings) as IModelDoc2;
                IDrawingDoc reopenedDrawing = reopenedModel as IDrawingDoc;
                if (reopenedDrawing == null || !reopenedModel.IsOpenedReadOnly())
                    return Fail("DIMENSION_DRAWING_REOPEN_FAILED", "",
                        "The saved drawing could not be reopened read-only.", result, out error);
                reopenedModel.ForceRebuild3(false);
                stage = "verify_persisted_drawing";
                JObject persisted;
                if (!_executor.TryVerifyPersisted(reopenedModel, reopenedDrawing, plan,
                    created.BaselineCount, created.Handles, created.PersistenceFingerprints,
                    out persisted, out nativeError))
                    return Fail(nativeError.Code, DimensionPointer(nativeError.DimensionId),
                        nativeError.Message, result, out error);
                result["reopen_verification"] = persisted;
                Close(ref reopenedModel);

                bool sourceSaveFlagAfterOperation = sourceModel.GetSaveFlag();
                Close(ref sourceModel);
                if (!FrozenHashesEqual(before, FrozenHashes(plan, paths.PlanPath)))
                    return Fail("DIMENSION_FROZEN_INPUT_MUTATED", "",
                        "A frozen input changed during dimension creation.", result, out error);

                // InsertModelAnnotations3 can mark its read-only referenced model dirty in memory
                // even though no source bytes are written. Discard that task-owned read-only RCW,
                // reopen the source from disk, and require both the frozen hash and a clean reopen.
                // This preserves the no-source-write invariant without weakening it to a stale
                // in-session save flag.
                stage = "verify_source_model_clean_reopen";
                errors = 0; warnings = 0;
                sourceModel = _solidWorks.OpenDoc6(plan.SourceModel.Path,
                    (int)swDocumentTypes_e.swDocPART,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    plan.Configuration, ref errors, ref warnings) as IModelDoc2;
                if (sourceModel == null || !sourceModel.IsOpenedReadOnly())
                    return Fail("DIMENSION_SOURCE_MODEL_REOPEN_FAILED", "",
                        "The unchanged source model could not be reopened read-only.", result, out error);
                bool sourceSaveFlagAfterReopen = sourceModel.GetSaveFlag();
                result["source_model_state"] = new JObject
                {
                    ["opened_read_only"] = true,
                    ["save_flag_before"] = sourceSaveFlagBefore,
                    ["save_flag_after_dimension_import"] = sourceSaveFlagAfterOperation,
                    ["save_flag_after_discard_and_reopen"] = sourceSaveFlagAfterReopen,
                    ["frozen_hash_unchanged"] = true
                };
                if (sourceSaveFlagAfterReopen != sourceSaveFlagBefore)
                    return Fail("DIMENSION_SOURCE_MODEL_DIRTY", "",
                        "The source model did not return to its original save flag after read-only discard/reopen.",
                        result, out error);
                Close(ref sourceModel);

                stage = "commit_output_and_sidecar";
                string artifactHash = DimensionPlanContractValidator.FileSha256(temporaryDrawing);
                JObject audit = new JObject
                {
                    ["protocol_id"] = "solidworks-dimension-drawing-verification",
                    ["schema_version"] = "1.0", ["operation_id"] = operationId,
                    ["generated_at_utc"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                    ["plan_id"] = plan.PlanId, ["plan_file_path"] = paths.PlanPath,
                    ["plan_file_sha256"] = paths.PlanFileSha256,
                    ["plan_canonical_sha256"] = plan.PlanSha256,
                    ["output_path"] = paths.OutputPath, ["artifact_sha256"] = artifactHash,
                    ["verified"] = true,
                    ["dimension_handles"] = JObject.FromObject(created.Handles),
                    ["in_memory_verification"] = created.InMemoryVerification,
                    ["reopen_verification"] = persisted,
                    ["frozen_inputs"] = JObject.FromObject(before)
                };
                File.WriteAllText(temporaryReport, audit.ToString(Formatting.Indented) + System.Environment.NewLine,
                    new UTF8Encoding(false));
                if (File.Exists(paths.OutputPath) || File.Exists(paths.ReportPath))
                    return Fail("DIMENSION_OUTPUT_RACE", "",
                        "A final output appeared during execution; commit was refused.", result, out error);
                File.Move(temporaryDrawing, paths.OutputPath); outputMoved = true;
                File.Move(temporaryReport, paths.ReportPath); reportMoved = true;
                complete = true;
                result["committed"] = true; result["output_path"] = paths.OutputPath;
                result["verification_path"] = paths.ReportPath;
                result["artifact_sha256"] = artifactHash;
                return true;
            }
            catch (Exception ex)
            { return Fail("DIMENSION_TRANSACTION_FAILED", "", stage + ": " + ex.Message,
                result, out error); }
            finally
            {
                Close(ref reopenedModel); Close(ref drawingModel); Close(ref sourceModel);
                // The transaction starts only from a clean SolidWorks session and therefore owns
                // documents opened at these two paths. A reopened drawing can keep its referenced
                // model loaded after the original COM wrapper is released, so close by exact path
                // as a second bounded cleanup pass instead of leaving a business document open.
                CloseByPath(temporaryDrawing);
                CloseByPath(plan.SourceModel.Path);
                if (!complete)
                {
                    TryDelete(temporaryDrawing); TryDelete(temporaryReport);
                    if (reportMoved) TryDelete(paths.ReportPath);
                    if (outputMoved) TryDelete(paths.OutputPath);
                }
            }
        }

        private static System.Collections.Generic.Dictionary<string, string> FrozenHashes(
            DimensionPlanExecutionPlan plan, string planPath)
        {
            return new System.Collections.Generic.Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["dimension_plan"] = DimensionPlanContractValidator.FileSha256(planPath),
                ["handoff"] = DimensionPlanContractValidator.FileSha256(plan.Handoff.Path),
                ["source_model"] = DimensionPlanContractValidator.FileSha256(plan.SourceModel.Path),
                ["source_drawing"] = DimensionPlanContractValidator.FileSha256(plan.SourceDrawing.Path),
                ["view_plan"] = DimensionPlanContractValidator.FileSha256(plan.ViewPlan.Path),
                ["verification_sidecar"] = DimensionPlanContractValidator.FileSha256(plan.VerificationSidecar.Path)
            };
        }
        private static bool FrozenHashesEqual(
            System.Collections.Generic.IDictionary<string, string> first,
            System.Collections.Generic.IDictionary<string, string> second) =>
            first.Count == second.Count && first.All(pair => second.ContainsKey(pair.Key) &&
                string.Equals(pair.Value, second[pair.Key], StringComparison.OrdinalIgnoreCase));
        private void Close(ref IModelDoc2 document)
        { if (document == null) return; try { _solidWorks.CloseDoc(document.GetTitle()); } catch { }
            document = null; }
        private void CloseByPath(string path)
        {
            if (String.IsNullOrWhiteSpace(path)) return;
            for (int attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    IModelDoc2 document = _solidWorks.GetOpenDocumentByName(path) as IModelDoc2;
                    if (document == null) return;
                    _solidWorks.CloseDoc(document.GetTitle());
                }
                catch { return; }
            }
        }
        private static void TryDelete(string path)
        { try { if (!string.IsNullOrEmpty(path) && File.Exists(path)) File.Delete(path); } catch { } }
        private static string DimensionPointer(string id) => string.IsNullOrEmpty(id) ? "" : id;
        private static bool Fail(string code, string pointer, string message, JObject result,
            out DimensionPlanTransactionError error)
        { error = new DimensionPlanTransactionError { Code = code, JsonPointer = pointer, Message = message };
            result["error_code"] = code; result["json_pointer"] = pointer; return false; }
    }

    internal sealed class DimensionPlanTransactionError
    { public string Code, JsonPointer, Message; }
}
