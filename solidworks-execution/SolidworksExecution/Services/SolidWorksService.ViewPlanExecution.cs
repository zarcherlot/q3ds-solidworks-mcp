using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Repository-native B4 semantic execution entries. Controller-visible operation names remain
    /// private executor protocol; the Agent sees the constrained create/verify engineering tools
    /// implemented by the Python semantic adapter.
    /// </summary>
    public partial class SolidWorksService
    {
        public ExecutionResponse ExecutePartDrawingViewPlan(ToolRequest request)
        {
            if (_guard.IsDuplicate(request.OperationId))
                return _guard.GetDuplicate(request.OperationId);
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");

            ViewPlanBasicExecutionPlan plan;
            string outputPath;
            ViewPlanExecutionContractError contractError;
            if (!TryParseViewPlanExecutionRequest(request.Params as JObject, out plan,
                out outputPath, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);

            ViewPlanBasicTransactionPaths ignored;
            if (!new ViewPlanBasicTransactionPreflight().TryValidate(plan, outputPath,
                out ignored, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);
            if (!new ViewPlanSectionGeometryResolver().TryResolve(plan, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);
            if (!new ViewPlanCenterGeometryResolver().TryResolve(plan, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");

            JObject transactionResult;
            ViewPlanBasicDrawingTransactionError transactionError;
            if (!new ViewPlanBasicDrawingTransaction(_solidWorks).TryExecute(plan, outputPath,
                request.OperationId, out transactionResult, out transactionError))
                return BuildViewPlanFailure(request.OperationId,
                    new ViewPlanExecutionContractError
                    {
                        Code = transactionError.Code,
                        JsonPointer = transactionError.JsonPointer,
                        Message = transactionError.Message
                    }, transactionResult);

            int nextState = _guard.GetCurrentStateVersion() + 1;
            var response = new ExecutionResponse
            {
                OperationId = request.OperationId,
                Status = "COMPLETED",
                Verified = true,
                StateVersion = nextState,
                LastKnownStateVersion = nextState,
                CadState = BuildCurrentCadState(nextState),
                ResultGeometry = transactionResult
            };
            if (response.CadState != null)
                response.CadState.Features = new List<string> { ignored.OutputPath };
            _guard.RegisterCompleted(request.OperationId, response);
            return response;
        }

        public ExecutionResponse VerifyCommittedPartDrawingViewPlan(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");

            ViewPlanBasicExecutionPlan plan;
            string outputPath;
            ViewPlanExecutionContractError contractError;
            if (!TryParseViewPlanExecutionRequest(request.Params as JObject, out plan,
                out outputPath, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);

            ViewPlanBasicVerificationInputs ignored;
            if (!new ViewPlanBasicVerificationPreflight().TryValidate(plan, outputPath,
                out ignored, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);
            if (!new ViewPlanSectionGeometryResolver().TryResolve(plan, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);
            if (!new ViewPlanCenterGeometryResolver().TryResolve(plan, out contractError))
                return BuildViewPlanFailure(request.OperationId, contractError, null);
            if (!EnsureConnected())
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "COM_ATTACH_FAILED", "SolidWorks process not found or COM not registered.");

            JObject verificationResult;
            ViewPlanBasicDrawingVerificationError verificationError;
            if (!new ViewPlanBasicDrawingVerifier(_solidWorks).TryVerify(plan, outputPath,
                out verificationResult, out verificationError))
                return BuildViewPlanFailure(request.OperationId,
                    new ViewPlanExecutionContractError
                    {
                        Code = verificationError.Code,
                        JsonPointer = verificationError.JsonPointer,
                        Message = verificationError.Message
                    }, verificationResult);

            int state = _guard.GetCurrentStateVersion();
            return new ExecutionResponse
            {
                OperationId = request.OperationId,
                Status = "COMPLETED",
                Verified = true,
                StateVersion = state,
                LastKnownStateVersion = state,
                CadState = BuildCurrentCadState(state),
                ResultGeometry = verificationResult
            };
        }

        private static bool TryParseViewPlanExecutionRequest(JObject parameters,
            out ViewPlanBasicExecutionPlan plan, out string outputPath,
            out ViewPlanExecutionContractError error)
        {
            plan = null;
            outputPath = null;
            error = null;
            if (parameters == null)
                return ViewPlanRequestFail("INVALID_VIEW_PLAN_REQUEST", "",
                    "params must be an object containing plan and output_path.", out error);
            var names = new HashSet<string>(parameters.Properties().Select(item => item.Name),
                StringComparer.Ordinal);
            if (!names.SetEquals(new[] { "plan", "output_path" }))
                return ViewPlanRequestFail("INVALID_VIEW_PLAN_REQUEST", "",
                    "params must contain exactly plan and output_path.", out error);
            var candidate = parameters["plan"] as JObject;
            if (candidate == null)
                return ViewPlanRequestFail("INVALID_VIEW_PLAN_REQUEST", "/plan",
                    "plan must be a structured JSON object.", out error);
            outputPath = parameters.Value<string>("output_path");
            if (string.IsNullOrWhiteSpace(outputPath))
                return ViewPlanRequestFail("INVALID_VIEW_PLAN_REQUEST", "/output_path",
                    "output_path must be a non-empty absolute .SLDDRW path.", out error);

            ViewPlanContractValidator validator;
            try { validator = ViewPlanContract.Value; }
            catch (Exception ex)
            {
                return ViewPlanRequestFail("VIEW_PLAN_CONTRACT_UNAVAILABLE", "", ex.Message,
                    out error);
            }
            ViewPlanDocument document;
            ViewPlanContractError schemaError;
            if (!validator.TryParse(candidate, out document, out schemaError))
                return ViewPlanRequestFail(schemaError.Code, schemaError.JsonPointer,
                    schemaError.Message, out error);
            if (!new ViewPlanBasicExecutionCompiler().TryCompile(document, out plan, out error))
                return false;
            return true;
        }

        private ExecutionResponse BuildViewPlanFailure(string operationId,
            ViewPlanExecutionContractError error, JObject diagnostics)
        {
            int state = _guard.GetCurrentStateVersion();
            var result = diagnostics ?? new JObject();
            result["json_pointer"] = error != null ? error.JsonPointer : "";
            return new ExecutionResponse
            {
                OperationId = operationId,
                Status = "FAILED",
                Verified = false,
                StateVersion = state,
                LastKnownStateVersion = state,
                ResultGeometry = result,
                Error = new ExecutionError
                {
                    Code = error != null ? error.Code : "VIEW_PLAN_EXECUTION_FAILED",
                    Message = error != null ? error.Message : "ViewPlan execution failed."
                }
            };
        }

        private static bool ViewPlanRequestFail(string code, string pointer, string message,
            out ViewPlanExecutionContractError error)
        {
            error = new ViewPlanExecutionContractError
            {
                Code = code,
                JsonPointer = pointer,
                Message = message
            };
            return false;
        }
    }
}
