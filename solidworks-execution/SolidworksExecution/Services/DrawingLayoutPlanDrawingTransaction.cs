using System;
using System.Collections.Generic;
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
    /// <summary>G4 atomic no-overwrite layout transaction and persisted readback.</summary>
    internal sealed class DrawingLayoutPlanDrawingTransaction
    {
        private readonly ISldWorks _solidWorks;
        private readonly DrawingLayoutPlanNativeExecutor _executor =
            new DrawingLayoutPlanNativeExecutor();

        public DrawingLayoutPlanDrawingTransaction(ISldWorks solidWorks)
        { _solidWorks = solidWorks ?? throw new ArgumentNullException("solidWorks"); }

        public bool TryExecute(DrawingLayoutExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, string operationId,
            out JObject result, out DrawingLayoutTransactionError error)
        {
            result = new JObject { ["committed"] = false }; error = null;
            DrawingLayoutTransactionPaths paths; DrawingLayoutPlanContractError preflight;
            if (!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                planSha256, requestedOutputPath, out paths, out preflight))
                return Fail(preflight.Code, preflight.JsonPointer, preflight.Message,
                    result, out error);
            string directory = Path.GetDirectoryName(paths.OutputPath);
            string stem = Path.GetFileNameWithoutExtension(paths.OutputPath);
            string nonce = Guid.NewGuid().ToString("N");
            string temporaryDrawing = Path.Combine(directory, stem + ".q3ds-layout-" +
                nonce + ".SLDDRW");
            string temporaryReport = Path.Combine(directory, "." + stem +
                ".q3ds-layout-" + nonce + ".tmp.json");
            IModelDoc2 drawingModel = null, reopenedModel = null;
            bool outputMoved = false, reportMoved = false, complete = false;
            string stage = "preflight";
            Dictionary<string, string> frozen = FrozenHashes(plan, paths.PlanPath);
            try
            {
                if (_solidWorks.IGetFirstDocument2() != null)
                    return Fail("DRAWING_LAYOUT_COPY_REQUIRES_NO_OPEN_DOCUMENTS", "",
                        "Layout CopyDocument requires a clean SolidWorks session.", result, out error);
                stage = "copy_dimensioned_drawing";
                int copy = _solidWorks.CopyDocument(plan.SourceDrawing.Path, temporaryDrawing,
                    null, null, 0);
                result["copy"] = new JObject { ["strategy"] = "solidworks_copy_document",
                    ["result"] = copy, ["temporary_exists"] = File.Exists(temporaryDrawing) };
                if (copy != (int)swMoveCopyError_e.swMoveCopyErrorNone ||
                    !File.Exists(temporaryDrawing))
                    return Fail("DRAWING_LAYOUT_COPY_FAILED", "",
                        "SolidWorks CopyDocument failed (result=" + copy + ").", result, out error);
                if (!FrozenEqual(frozen, FrozenHashes(plan, paths.PlanPath)))
                    return Fail("DRAWING_LAYOUT_FROZEN_INPUT_MUTATED", "",
                        "A frozen input changed during CopyDocument.", result, out error);

                stage = "open_transaction_drawing";
                int errors = 0, warnings = 0;
                drawingModel = _solidWorks.OpenDoc6(temporaryDrawing,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                    "", ref errors, ref warnings) as IModelDoc2;
                IDrawingDoc drawing = drawingModel as IDrawingDoc;
                if (drawing == null || drawingModel.IsOpenedReadOnly())
                    return Fail("DRAWING_LAYOUT_OPEN_FAILED", "",
                        "Transaction drawing could not be opened writable.", result, out error);
                stage = "capture_semantic_baseline";
                JObject baseline = _executor.CaptureDimensionSemantics(drawing);
                JObject baselineSnapshot = LayoutBoundaryProbeExecutor.CaptureSnapshot(
                    drawingModel, drawing, "g4_baseline");
                JArray baselineViewSemantics = DrawingLayoutPlanNativeExecutor
                    .CaptureViewSemantics(baselineSnapshot);
                if (baseline.Value<int>("count") != plan.DimensionIds.Count)
                    return Fail("DRAWING_LAYOUT_DIMENSION_COUNT_MISMATCH", "",
                        "Native dimension count differs from the frozen G1 dimension IDs.",
                        result, out error);

                stage = "apply_rebuild_readback";
                DrawingLayoutNativeResult applied; DrawingLayoutNativeError nativeError;
                if (!_executor.TryApply(drawingModel, drawing, plan, baseline,
                    baselineViewSemantics,
                    out applied, out nativeError))
                    return Fail(nativeError.Code, nativeError.JsonPointer,
                        nativeError.Message, result, out error);
                result["bounded_cycles"] = applied.Cycles;
                result["pre_save_verification"] = applied.Verification;

                stage = "save_transaction_drawing";
                drawingModel.ClearSelection2(true);
                bool rebuilt = drawingModel.ForceRebuild3(false);
                int saveErrors = 0, saveWarnings = 0;
                bool saved = drawingModel.Save3((int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                    ref saveErrors, ref saveWarnings);
                result["save"] = new JObject { ["rebuild"] = rebuilt, ["saved"] = saved,
                    ["errors"] = saveErrors, ["warnings"] = saveWarnings };
                if (!saved || saveErrors != 0 || !File.Exists(temporaryDrawing))
                    return Fail("DRAWING_LAYOUT_SAVE_FAILED", "",
                        "Save3 failed; the transaction will be rolled back.", result, out error);
                JObject postSave;
                if (!_executor.TryVerifyCurrent(drawingModel, drawing, plan, baseline,
                    baselineViewSemantics, "g4_post_save", out postSave, out nativeError))
                    return Fail(nativeError.Code, nativeError.JsonPointer,
                        nativeError.Message, result, out error);
                result["in_memory_verification"] = postSave;
                Close(ref drawingModel);
                if (_solidWorks.GetOpenDocumentByName(temporaryDrawing) != null)
                    return Fail("DRAWING_LAYOUT_CLOSE_FAILED", "",
                        "Transaction drawing remained open after CloseDoc.", result, out error);

                stage = "readonly_reopen";
                errors = 0; warnings = 0;
                reopenedModel = _solidWorks.OpenDoc6(temporaryDrawing,
                    (int)swDocumentTypes_e.swDocDRAWING,
                    (int)swOpenDocOptions_e.swOpenDocOptions_Silent |
                    (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly,
                    "", ref errors, ref warnings) as IModelDoc2;
                IDrawingDoc reopenedDrawing = reopenedModel as IDrawingDoc;
                if (reopenedDrawing == null || !reopenedModel.IsOpenedReadOnly())
                    return Fail("DRAWING_LAYOUT_REOPEN_FAILED", "",
                        "Saved layout drawing could not be reopened read-only.", result, out error);
                reopenedModel.ForceRebuild3(false);
                JObject persisted;
                if (!_executor.TryVerifyPersisted(reopenedModel, reopenedDrawing, plan, baseline,
                    baselineViewSemantics,
                    postSave.Value<string>("layout_fingerprint_sha256"),
                    out persisted, out nativeError))
                {
                    if (persisted != null)
                        result["failed_reopen_verification"] = persisted;
                    return Fail(nativeError.Code, nativeError.JsonPointer,
                        nativeError.Message, result, out error);
                }
                result["reopen_verification"] = persisted;
                Close(ref reopenedModel);
                if (!FrozenEqual(frozen, FrozenHashes(plan, paths.PlanPath)))
                    return Fail("DRAWING_LAYOUT_FROZEN_INPUT_MUTATED", "",
                        "A frozen input changed during layout execution.", result, out error);

                stage = "commit_output_and_sidecar";
                string artifactHash = DrawingLayoutPlanContractValidator.FileSha256(
                    temporaryDrawing);
                JObject audit = new JObject
                {
                    ["protocol_id"] = "solidworks-drawing-layout-verification",
                    ["schema_version"] = "1.0", ["operation_id"] = operationId,
                    ["generated_at_utc"] = DateTime.UtcNow.ToString("o",
                        CultureInfo.InvariantCulture), ["plan_id"] = plan.PlanId,
                    ["plan_file_path"] = paths.PlanPath,
                    ["plan_file_sha256"] = paths.PlanFileSha256,
                    ["plan_canonical_sha256"] = plan.PlanSha256,
                    ["source_drawing_path"] = plan.SourceDrawing.Path,
                    ["source_drawing_sha256"] = plan.SourceDrawing.Sha256,
                    ["output_path"] = paths.OutputPath, ["artifact_sha256"] = artifactHash,
                    ["verified"] = true, ["bounded_cycles"] = applied.Cycles,
                    ["in_memory_verification"] = postSave,
                    ["reopen_verification"] = persisted,
                    ["frozen_inputs"] = JObject.FromObject(frozen)
                };
                File.WriteAllText(temporaryReport, audit.ToString(Formatting.Indented) +
                    System.Environment.NewLine, new UTF8Encoding(false));
                if (File.Exists(paths.OutputPath) || File.Exists(paths.ReportPath))
                    return Fail("DRAWING_LAYOUT_OUTPUT_RACE", "",
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
            { return Fail("DRAWING_LAYOUT_TRANSACTION_FAILED", "", stage + ": " + ex.Message,
                result, out error); }
            finally
            {
                Close(ref reopenedModel); Close(ref drawingModel); CloseByPath(temporaryDrawing);
                if (!complete)
                {
                    TryDelete(temporaryDrawing); TryDelete(temporaryReport);
                    if (reportMoved) TryDelete(paths.ReportPath);
                    if (outputMoved) TryDelete(paths.OutputPath);
                }
            }
        }

        private static Dictionary<string, string> FrozenHashes(
            DrawingLayoutExecutionPlan plan, string planPath)
        {
            var hashes = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["drawing_layout_plan"] = DrawingLayoutPlanContractValidator.FileSha256(planPath),
                ["handoff"] = DrawingLayoutPlanContractValidator.FileSha256(plan.Handoff.Path),
                ["dimension_plan"] = DrawingLayoutPlanContractValidator.FileSha256(
                    plan.SourceDimensionPlan.Path),
                ["source_drawing"] = DrawingLayoutPlanContractValidator.FileSha256(
                    plan.SourceDrawing.Path),
                ["dimension_verification_sidecar"] =
                    DrawingLayoutPlanContractValidator.FileSha256(
                        plan.DimensionVerificationSidecar.Path)
            };
            foreach (KeyValuePair<string, DrawingLayoutArtifactBinding> pair in
                plan.UpstreamDimensionArtifacts)
                hashes["dimension_plan." + pair.Key] =
                    DrawingLayoutPlanContractValidator.FileSha256(pair.Value.Path);
            return hashes;
        }
        private static bool FrozenEqual(IDictionary<string, string> first,
            IDictionary<string, string> second) => first.Count == second.Count &&
            first.All(pair => second.ContainsKey(pair.Key) && String.Equals(pair.Value,
                second[pair.Key], StringComparison.OrdinalIgnoreCase));
        private void Close(ref IModelDoc2 document)
        { if (document == null) return; try { _solidWorks.CloseDoc(document.GetTitle()); } catch { }
            document = null; }
        private void CloseByPath(string path)
        { try { IModelDoc2 document = _solidWorks.GetOpenDocumentByName(path) as IModelDoc2;
            if (document != null) _solidWorks.CloseDoc(document.GetTitle()); } catch { } }
        private static void TryDelete(string path)
        { try { if (!String.IsNullOrEmpty(path) && File.Exists(path)) File.Delete(path); } catch { } }
        private static bool Fail(string code, string pointer, string message, JObject result,
            out DrawingLayoutTransactionError error)
        { error = new DrawingLayoutTransactionError { Code = code, JsonPointer = pointer,
            Message = message }; result["error_code"] = code; result["json_pointer"] = pointer;
            return false; }
    }

    internal sealed class DrawingLayoutTransactionError
    { public string Code, JsonPointer, Message; }
}
