using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    /// <summary>Private executor boundary for repository-owned DrawingLayoutPlan 1.0.</summary>
    public partial class SolidWorksService
    {
        private static readonly Lazy<DrawingLayoutPlanContractValidator> DrawingLayoutContract =
            new Lazy<DrawingLayoutPlanContractValidator>(() =>
                new DrawingLayoutPlanContractValidator(Path.Combine(
                    AppDomain.CurrentDomain.BaseDirectory, "contracts",
                    "drawing-layout-plan.schema.json")), true);
        private static string DrawingLayoutCapabilityRegistryPath => Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "contracts",
            "drawing-layout-plan-capabilities.json");
        private static string DrawingLayoutBoundaryRegistryPath => Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "contracts",
            "drawing-layout-executor-capabilities.json");

        public ExecutionResponse ValidatePartDrawingLayoutPlan(ToolRequest request)
        {
            DrawingLayoutExecutionPlan plan; string planPath, planSha, outputPath;
            DrawingLayoutPlanContractError error;
            if (!TryParseDrawingLayoutRequest(request.Params as JObject, out plan,
                out planPath, out planSha, out outputPath, out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            DrawingLayoutTransactionPaths paths;
            if (!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                planSha, outputPath, out paths, out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            bool supported = new DrawingLayoutPlanCapabilityPreflight().TryValidate(plan,
                DrawingLayoutCapabilityRegistryPath, DrawingLayoutBoundaryRegistryPath,
                out error);
            var result = new JObject
            {
                ["contract_valid"] = true, ["preflight_valid"] = true,
                ["protocol_id"] = DrawingLayoutPlanContractValidator.ProtocolId,
                ["schema_version"] = DrawingLayoutPlanContractValidator.SchemaVersion,
                ["schema_sha256"] = DrawingLayoutPlanContractValidator.ContractSha256,
                ["plan_id"] = plan.PlanId,
                ["plan_canonical_sha256"] = plan.PlanSha256,
                ["plan_file_sha256"] = paths.PlanFileSha256,
                ["execution_readiness"] = supported ? "supported" : "capability_blocked",
                ["solidworks_contacted"] = false
            };
            if (!supported) result["capability_blocker"] = new JObject
                { ["code"] = error.Code, ["json_pointer"] = error.JsonPointer,
                    ["message"] = error.Message };
            int state = _guard.GetCurrentStateVersion();
            return new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = state,
                LastKnownStateVersion = state, ResultGeometry = result };
        }

        public ExecutionResponse ExecutePartDrawingLayoutPlan(ToolRequest request)
        {
            if (_guard.IsDuplicate(request.OperationId)) return _guard.GetDuplicate(request.OperationId);
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            DrawingLayoutExecutionPlan plan; string planPath, planSha, outputPath;
            DrawingLayoutPlanContractError error;
            if (!TryParseDrawingLayoutRequest(request.Params as JObject, out plan,
                out planPath, out planSha, out outputPath, out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            DrawingLayoutTransactionPaths paths;
            if (!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                planSha, outputPath, out paths, out error) ||
                !new DrawingLayoutPlanCapabilityPreflight().TryValidate(plan,
                    DrawingLayoutCapabilityRegistryPath, DrawingLayoutBoundaryRegistryPath,
                    out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");
            JObject transaction; DrawingLayoutTransactionError transactionError;
            if (!new DrawingLayoutPlanDrawingTransaction(_solidWorks).TryExecute(plan, planPath,
                planSha, outputPath, request.OperationId, out transaction, out transactionError))
                return BuildDrawingLayoutFailure(request.OperationId,
                    new DrawingLayoutPlanContractError { Code = transactionError.Code,
                        JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message }, transaction);
            int next = _guard.GetCurrentStateVersion() + 1;
            var response = new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = next,
                LastKnownStateVersion = next, CadState = BuildCurrentCadState(next),
                ResultGeometry = transaction };
            _guard.RegisterCompleted(request.OperationId, response);
            return response;
        }

        public ExecutionResponse QualifyPartDrawingLayoutPlan(ToolRequest request)
        {
            if (_guard.IsDuplicate(request.OperationId)) return _guard.GetDuplicate(request.OperationId);
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            DrawingLayoutExecutionPlan plan; string planPath, planSha, outputPath;
            string matrixPath, matrixSha, requestSha, sourceRequestSha, caseId;
            DrawingLayoutPlanContractError error;
            if (!TryParseDrawingLayoutQualificationRequest(request.Params as JObject, out plan,
                out planPath, out planSha, out outputPath, out matrixPath, out matrixSha,
                out requestSha, out sourceRequestSha, out caseId, out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            DrawingLayoutTransactionPaths paths;
            if (!new DrawingLayoutPlanTransactionPreflight().TryValidate(plan, planPath,
                    planSha, outputPath, out paths, out error) ||
                !new DrawingLayoutPlanQualificationPreflight().TryValidate(plan, planPath,
                    planSha, outputPath, matrixPath, matrixSha, requestSha,
                    sourceRequestSha, caseId, out error) ||
                !new DrawingLayoutPlanCapabilityPreflight().TryValidateQualification(plan,
                    DrawingLayoutCapabilityRegistryPath, DrawingLayoutBoundaryRegistryPath,
                    out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");
            JObject transaction; DrawingLayoutTransactionError transactionError;
            if (!new DrawingLayoutPlanDrawingTransaction(_solidWorks).TryExecute(plan, planPath,
                planSha, outputPath, request.OperationId, out transaction, out transactionError))
                return BuildDrawingLayoutFailure(request.OperationId,
                    new DrawingLayoutPlanContractError { Code = transactionError.Code,
                        JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message }, transaction);
            transaction["qualification_scope"] = "g7_live_evidence_only";
            transaction["case_id"] = caseId;
            int next = _guard.GetCurrentStateVersion() + 1;
            var response = new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = next,
                LastKnownStateVersion = next, CadState = BuildCurrentCadState(next),
                ResultGeometry = transaction };
            _guard.RegisterCompleted(request.OperationId, response);
            return response;
        }

        public ExecutionResponse VerifyCommittedPartDrawingLayoutPlan(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            DrawingLayoutExecutionPlan plan; string planPath, planSha, outputPath;
            DrawingLayoutPlanContractError error;
            if (!TryParseDrawingLayoutRequest(request.Params as JObject, out plan,
                out planPath, out planSha, out outputPath, out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            DrawingLayoutVerificationInputs ignored;
            string dimensionSchema = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                "contracts", "dimension-plan.schema.json");
            string verificationSchema = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                "contracts", "drawing-layout-verification.schema.json");
            if (!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                planSha, outputPath, dimensionSchema, verificationSchema, out ignored,
                out error) || !new DrawingLayoutPlanCapabilityPreflight().TryValidate(plan,
                    DrawingLayoutCapabilityRegistryPath, DrawingLayoutBoundaryRegistryPath,
                    out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");
            JObject verification; DrawingLayoutTransactionError transactionError;
            if (!new DrawingLayoutPlanDrawingVerifier(_solidWorks).TryVerify(plan, planPath,
                planSha, outputPath, dimensionSchema, verificationSchema,
                out verification, out transactionError))
                return BuildDrawingLayoutFailure(request.OperationId,
                    new DrawingLayoutPlanContractError { Code = transactionError.Code,
                        JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message }, verification);
            int state = _guard.GetCurrentStateVersion();
            return new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = state,
                LastKnownStateVersion = state, CadState = BuildCurrentCadState(state),
                ResultGeometry = verification };
        }

        public ExecutionResponse VerifyQualifiedPartDrawingLayoutPlan(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            DrawingLayoutExecutionPlan plan; string planPath, planSha, outputPath;
            string matrixPath, matrixSha, requestSha, sourceRequestSha, caseId;
            DrawingLayoutPlanContractError error;
            if (!TryParseDrawingLayoutQualificationRequest(request.Params as JObject, out plan,
                out planPath, out planSha, out outputPath, out matrixPath, out matrixSha,
                out requestSha, out sourceRequestSha, out caseId, out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            DrawingLayoutVerificationInputs ignored;
            string dimensionSchema = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                "contracts", "dimension-plan.schema.json");
            string verificationSchema = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                "contracts", "drawing-layout-verification.schema.json");
            if (!new DrawingLayoutPlanVerificationPreflight().TryValidate(plan, planPath,
                    planSha, outputPath, dimensionSchema, verificationSchema, out ignored,
                    out error) ||
                !new DrawingLayoutPlanQualificationPreflight().TryValidate(plan, planPath,
                    planSha, outputPath, matrixPath, matrixSha, requestSha,
                    sourceRequestSha, caseId, out error) ||
                !new DrawingLayoutPlanCapabilityPreflight().TryValidateQualification(plan,
                    DrawingLayoutCapabilityRegistryPath, DrawingLayoutBoundaryRegistryPath,
                    out error))
                return BuildDrawingLayoutFailure(request.OperationId, error, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");
            JObject verification; DrawingLayoutTransactionError transactionError;
            if (!new DrawingLayoutPlanDrawingVerifier(_solidWorks).TryVerify(plan, planPath,
                planSha, outputPath, dimensionSchema, verificationSchema,
                out verification, out transactionError))
                return BuildDrawingLayoutFailure(request.OperationId,
                    new DrawingLayoutPlanContractError { Code = transactionError.Code,
                        JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message }, verification);
            verification["qualification_scope"] = "g7_live_evidence_only";
            verification["case_id"] = caseId;
            int state = _guard.GetCurrentStateVersion();
            return new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = state,
                LastKnownStateVersion = state, CadState = BuildCurrentCadState(state),
                ResultGeometry = verification };
        }

        private static bool TryParseDrawingLayoutRequest(JObject parameters,
            out DrawingLayoutExecutionPlan plan, out string planPath, out string planSha,
            out string outputPath, out DrawingLayoutPlanContractError error)
        {
            plan = null; planPath = null; planSha = null; outputPath = null; error = null;
            if (parameters == null || !new HashSet<string>(parameters.Properties().Select(
                property => property.Name), StringComparer.Ordinal).SetEquals(new[]
                    { "plan", "plan_path", "plan_sha256", "plan_canonical_sha256",
                      "output_path" }))
                return LayoutRequestFail("INVALID_DRAWING_LAYOUT_REQUEST", "",
                    "params must contain the plan, file/canonical hashes and output_path.",
                    out error);
            JObject candidate = parameters["plan"] as JObject;
            planPath = parameters.Value<string>("plan_path");
            planSha = parameters.Value<string>("plan_sha256");
            string planCanonicalSha = parameters.Value<string>("plan_canonical_sha256");
            outputPath = parameters.Value<string>("output_path");
            if (String.IsNullOrEmpty(planCanonicalSha) || planCanonicalSha.Length != 64 ||
                planCanonicalSha.Any(character => !((character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f'))))
                return LayoutRequestFail("INVALID_DRAWING_LAYOUT_REQUEST",
                    "/plan_canonical_sha256", "canonical hash must be lowercase SHA-256.",
                    out error);
            DrawingLayoutPlanDocument document;
            try
            {
                if (!DrawingLayoutContract.Value.TryParse(candidate, out document, out error))
                    return false;
            }
            catch (Exception ex)
            { return LayoutRequestFail("DRAWING_LAYOUT_CONTRACT_UNAVAILABLE", "", ex.Message,
                out error); }
            if (!new DrawingLayoutPlanExecutionCompiler().TryCompile(document, out plan,
                out error)) return false;
            plan.PlanSha256 = planCanonicalSha;
            return true;
        }

        private static bool TryParseDrawingLayoutQualificationRequest(JObject parameters,
            out DrawingLayoutExecutionPlan plan, out string planPath, out string planSha,
            out string outputPath, out string matrixPath, out string matrixSha,
            out string requestSha, out string sourceRequestSha, out string caseId,
            out DrawingLayoutPlanContractError error)
        {
            plan = null; planPath = null; planSha = null; outputPath = null;
            matrixPath = null; matrixSha = null; requestSha = null;
            sourceRequestSha = null; caseId = null; error = null;
            if (parameters == null || !new HashSet<string>(parameters.Properties().Select(
                property => property.Name), StringComparer.Ordinal).SetEquals(new[]
                    { "plan", "plan_path", "plan_sha256", "plan_canonical_sha256",
                      "output_path",
                      "matrix_request_path", "matrix_request_sha256",
                      "planning_request_sha256", "source_dimension_request_sha256", "case_id" }))
                return LayoutRequestFail("INVALID_DRAWING_LAYOUT_QUALIFICATION_REQUEST", "",
                    "Qualification params do not match the frozen G7 contract.", out error);
            var core = new JObject { ["plan"] = parameters["plan"].DeepClone(),
                ["plan_path"] = parameters["plan_path"].DeepClone(),
                ["plan_sha256"] = parameters["plan_sha256"].DeepClone(),
                ["plan_canonical_sha256"] =
                    parameters["plan_canonical_sha256"].DeepClone(),
                ["output_path"] = parameters["output_path"].DeepClone() };
            if (!TryParseDrawingLayoutRequest(core, out plan, out planPath, out planSha,
                out outputPath, out error)) return false;
            matrixPath = parameters.Value<string>("matrix_request_path");
            matrixSha = parameters.Value<string>("matrix_request_sha256");
            requestSha = parameters.Value<string>("planning_request_sha256");
            sourceRequestSha = parameters.Value<string>("source_dimension_request_sha256");
            caseId = parameters.Value<string>("case_id");
            return true;
        }

        private ExecutionResponse BuildDrawingLayoutFailure(string operationId,
            DrawingLayoutPlanContractError error, JObject diagnostics)
        {
            int state = _guard.GetCurrentStateVersion(); JObject result = diagnostics ?? new JObject();
            result["json_pointer"] = error != null ? error.JsonPointer : "";
            return new ExecutionResponse { OperationId = operationId, Status = "FAILED",
                Verified = false, StateVersion = state, LastKnownStateVersion = state,
                ResultGeometry = result, Error = new ExecutionError
                { Code = error != null ? error.Code : "DRAWING_LAYOUT_EXECUTION_FAILED",
                    Message = error != null ? error.Message : "Drawing layout execution failed." } };
        }
        private static bool LayoutRequestFail(string code, string pointer, string message,
            out DrawingLayoutPlanContractError error)
        { error = new DrawingLayoutPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }
}
