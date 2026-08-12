using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Repository-native ViewPlan 1.4 protocol boundary. B1 is deliberately COM-free: it proves
    /// that the C# executor can independently parse the complete frozen contract before any view
    /// creation capability is enabled.
    /// </summary>
    public partial class SolidWorksService
    {
        private static readonly Lazy<ViewPlanContractValidator> ViewPlanContract =
            new Lazy<ViewPlanContractValidator>(() => new ViewPlanContractValidator(
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                    "contracts", "view-plan.schema.json")), true);

        public ExecutionResponse ValidatePartDrawingViewPlan(ToolRequest request)
        {
            if (!_guard.IsStateVersionValid(request.StateVersion))
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_STATE_VERSION", "Incoming state_version does not match current state.");

            var parameters = request.Params as JObject;
            if (parameters == null)
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_VIEW_PLAN_REQUEST", "params must be an object containing plan.");
            foreach (var property in parameters.Properties())
                if (!string.Equals(property.Name, "plan", StringComparison.Ordinal))
                    return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                        "INVALID_VIEW_PLAN_REQUEST",
                        "Unknown request parameter '" + property.Name + "'.");
            JToken candidate = parameters["plan"];
            if (candidate == null || candidate.Type != JTokenType.Object)
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "INVALID_VIEW_PLAN_REQUEST", "plan must be a structured JSON object.");

            ViewPlanContractValidator validator;
            try
            {
                validator = ViewPlanContract.Value;
            }
            catch (Exception ex)
            {
                return BuildFailed(request.OperationId, _guard.GetCurrentStateVersion(),
                    "VIEW_PLAN_CONTRACT_UNAVAILABLE", ex.Message);
            }

            ViewPlanDocument plan;
            ViewPlanContractError validationError;
            if (!validator.TryParse(candidate, out plan, out validationError))
            {
                var details = new JObject();
                details["json_pointer"] = validationError.JsonPointer;
                return new ExecutionResponse
                {
                    OperationId = request.OperationId,
                    Status = "FAILED",
                    Verified = false,
                    StateVersion = _guard.GetCurrentStateVersion(),
                    LastKnownStateVersion = _guard.GetCurrentStateVersion(),
                    ResultGeometry = details,
                    Error = new ExecutionError
                    {
                        Code = validationError.Code,
                        Message = validationError.Message
                    }
                };
            }

            var result = new JObject();
            result["contract_valid"] = true;
            result["protocol_id"] = plan.ProtocolId;
            result["schema_version"] = plan.SchemaVersion;
            result["schema_sha256"] = ViewPlanContractValidator.ContractSha256;
            result["plan_id"] = plan.PlanId;
            result["plan_canonical_sha256"] = plan.CanonicalSha256;
            result["main_view_id"] = plan.MainViewId;
            result["view_count"] = plan.ViewTypes.Length;
            result["view_types"] = new JArray(plan.ViewTypes);
            result["execution_readiness"] = "not_assessed";
            var basicCompiler = new ViewPlanBasicExecutionCompiler();
            ViewPlanBasicExecutionPlan basicPlan;
            ViewPlanExecutionContractError basicError;
            if (basicCompiler.TryCompile(plan, out basicPlan, out basicError))
            {
                result["execution_readiness"] = "supported";
                result["b2_basic_view_subset"] = "compilable";
                result["b2_creation_order"] = new JArray(
                    Array.ConvertAll(basicPlan.Views.ToArray(), item => item.Id));
                result["native_view_subset"] = "compilable";
                result["native_creation_order"] = new JArray(
                    Array.ConvertAll(basicPlan.Views.ToArray(), item => item.Id));
            }
            else
            {
                result["execution_readiness"] = "capability_blocked";
                result["b2_basic_view_subset"] = "blocked";
                result["native_view_subset"] = "blocked";
                result["b2_blocker"] = new JObject
                {
                    ["code"] = basicError.Code,
                    ["json_pointer"] = basicError.JsonPointer,
                    ["message"] = basicError.Message
                };
                result["native_blocker"] = result["b2_blocker"].DeepClone();
            }
            result["solidworks_contacted"] = false;
            return new ExecutionResponse
            {
                OperationId = request.OperationId,
                Status = "COMPLETED",
                Verified = true,
                StateVersion = _guard.GetCurrentStateVersion(),
                LastKnownStateVersion = _guard.GetCurrentStateVersion(),
                CadState = null,
                ResultGeometry = result
            };
        }
    }
}
