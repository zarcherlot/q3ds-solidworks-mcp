using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    /// <summary>Private executor boundary for the repository-owned DimensionPlan 1.0.</summary>
    public partial class SolidWorksService
    {
        private static readonly Lazy<DimensionPlanContractValidator> DimensionPlanContract =
            new Lazy<DimensionPlanContractValidator>(() => new DimensionPlanContractValidator(
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "contracts",
                    "dimension-plan.schema.json")), true);
        private static string DimensionCapabilityRegistryPath => Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "contracts",
            "dimension-executor-capabilities.json");

        public ExecutionResponse ValidatePartDrawingDimensionPlan(ToolRequest request)
        {
            DimensionPlanExecutionPlan plan; string planPath, planSha, outputPath;
            DimensionPlanContractError error;
            if (!TryParseDimensionRequest(request.Params as JObject, out plan, out planPath,
                out planSha, out outputPath, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            DimensionPlanTransactionPaths paths;
            if (!new DimensionPlanTransactionPreflight().TryValidate(plan, planPath, planSha,
                outputPath, out paths, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            bool supported = new DimensionPlanCapabilityPreflight().TryValidate(plan,
                DimensionCapabilityRegistryPath, out error);
            var result = new JObject
            {
                ["contract_valid"] = true, ["preflight_valid"] = true,
                ["protocol_id"] = "solidworks-dimension-plan", ["schema_version"] = "1.0",
                ["schema_sha256"] = DimensionPlanContractValidator.ContractSha256,
                ["plan_id"] = plan.PlanId, ["plan_canonical_sha256"] = plan.PlanSha256,
                ["plan_file_sha256"] = paths.PlanFileSha256,
                ["execution_readiness"] = supported ? "supported" : "capability_blocked",
                ["solidworks_contacted"] = false
            };
            if (!supported)
                result["capability_blocker"] = new JObject { ["code"] = error.Code,
                    ["json_pointer"] = error.JsonPointer, ["message"] = error.Message };
            int state = _guard.GetCurrentStateVersion();
            return new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = state,
                LastKnownStateVersion = state, ResultGeometry = result };
        }

        public ExecutionResponse ExecutePartDrawingDimensionPlan(ToolRequest request)
        {
            if (_guard.IsDuplicate(request.OperationId)) return _guard.GetDuplicate(request.OperationId);
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            DimensionPlanExecutionPlan plan; string planPath, planSha, outputPath;
            DimensionPlanContractError error;
            if (!TryParseDimensionRequest(request.Params as JObject, out plan, out planPath,
                out planSha, out outputPath, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            DimensionPlanTransactionPaths ignored;
            if (!new DimensionPlanTransactionPreflight().TryValidate(plan, planPath, planSha,
                outputPath, out ignored, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            if (!new DimensionPlanCapabilityPreflight().TryValidate(plan,
                DimensionCapabilityRegistryPath, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");
            JObject transaction; DimensionPlanTransactionError transactionError;
            if (!new DimensionPlanDrawingTransaction(_solidWorks).TryExecute(plan, planPath,
                planSha, outputPath, request.OperationId, out transaction, out transactionError))
                return BuildDimensionFailure(request.OperationId, new DimensionPlanContractError
                    { Code = transactionError.Code, JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message }, transaction);
            int next = _guard.GetCurrentStateVersion() + 1;
            var response = new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = next,
                LastKnownStateVersion = next, CadState = BuildCurrentCadState(next),
                ResultGeometry = transaction };
            _guard.RegisterCompleted(request.OperationId, response);
            return response;
        }

        public ExecutionResponse VerifyCommittedPartDrawingDimensionPlan(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");
            DimensionPlanExecutionPlan plan; string planPath, planSha, outputPath;
            DimensionPlanContractError error;
            if (!TryParseDimensionRequest(request.Params as JObject, out plan, out planPath,
                out planSha, out outputPath, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            DimensionPlanVerificationInputs ignored;
            if (!new DimensionPlanVerificationPreflight().TryValidate(plan, planPath, planSha,
                outputPath, out ignored, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            if (!new DimensionPlanCapabilityPreflight().TryValidate(plan,
                DimensionCapabilityRegistryPath, out error))
                return BuildDimensionFailure(request.OperationId, error, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");
            JObject verification; DimensionPlanTransactionError transactionError;
            if (!new DimensionPlanDrawingVerifier(_solidWorks).TryVerify(plan, planPath,
                planSha, outputPath, out verification, out transactionError))
                return BuildDimensionFailure(request.OperationId, new DimensionPlanContractError
                    { Code = transactionError.Code, JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message }, verification);
            int state = _guard.GetCurrentStateVersion();
            return new ExecutionResponse { OperationId = request.OperationId,
                Status = "COMPLETED", Verified = true, StateVersion = state,
                LastKnownStateVersion = state, CadState = BuildCurrentCadState(state),
                ResultGeometry = verification };
        }

        private static bool TryParseDimensionRequest(JObject parameters,
            out DimensionPlanExecutionPlan plan, out string planPath, out string planSha,
            out string outputPath, out DimensionPlanContractError error)
        {
            plan = null; planPath = null; planSha = null; outputPath = null; error = null;
            if (parameters == null || !new HashSet<string>(parameters.Properties().Select(p => p.Name),
                StringComparer.Ordinal).SetEquals(new[] { "plan", "plan_path", "plan_sha256", "output_path" }))
                return RequestFail("INVALID_DIMENSION_PLAN_REQUEST", "",
                    "params must contain exactly plan, plan_path, plan_sha256 and output_path.", out error);
            JObject candidate = parameters["plan"] as JObject;
            if (candidate == null) return RequestFail("INVALID_DIMENSION_PLAN_REQUEST", "/plan",
                "plan must be a structured JSON object.", out error);
            planPath = parameters.Value<string>("plan_path");
            planSha = parameters.Value<string>("plan_sha256");
            outputPath = parameters.Value<string>("output_path");
            DimensionPlanDocument document; DimensionPlanContractError schemaError;
            try
            {
                if (!DimensionPlanContract.Value.TryParse(candidate, out document, out schemaError))
                { error = schemaError; return false; }
            }
            catch (Exception ex)
            { return RequestFail("DIMENSION_PLAN_CONTRACT_UNAVAILABLE", "", ex.Message, out error); }
            return new DimensionPlanExecutionCompiler().TryCompile(document, out plan, out error);
        }

        private ExecutionResponse BuildDimensionFailure(string operationId,
            DimensionPlanContractError error, JObject diagnostics)
        {
            int state = _guard.GetCurrentStateVersion(); JObject result = diagnostics ?? new JObject();
            result["json_pointer"] = error != null ? error.JsonPointer : "";
            return new ExecutionResponse { OperationId = operationId, Status = "FAILED",
                Verified = false, StateVersion = state, LastKnownStateVersion = state,
                ResultGeometry = result, Error = new ExecutionError
                { Code = error != null ? error.Code : "DIMENSION_PLAN_EXECUTION_FAILED",
                    Message = error != null ? error.Message : "DimensionPlan execution failed." } };
        }
        private static bool RequestFail(string code, string pointer, string message,
            out DimensionPlanContractError error)
        { error = new DimensionPlanContractError { Code = code, JsonPointer = pointer, Message = message };
            return false; }
    }
}
