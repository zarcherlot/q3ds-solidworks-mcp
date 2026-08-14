using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    public partial class SolidWorksService
    {
        public JObject RunManagedDimensionPlanningHandoff(
            DimensionPlanningHandoffRequest request)
        {
            Dictionary<string, object> readiness = EnsureReady();
            bool attached = readiness.ContainsKey("comAttached") &&
                Convert.ToBoolean(readiness["comAttached"]);
            int ownedProcessId;
            bool ownsSession = TryGetOwnedSolidWorksProcessId(out ownedProcessId);
            var lifecycle = new JObject
            {
                ["ensure_ready"] = JObject.FromObject(readiness),
                ["session_owned_by_execution_service"] = ownsSession,
                ["owned_process_id"] = ownsSession ?
                    (JToken)ownedProcessId : JValue.CreateNull()
            };
            if (!attached)
            {
                lifecycle["cleanup"] = ownsSession
                    ? CleanupOwnedSolidWorksSession()
                    : SkippedCleanup("SolidWorks was not attached.");
                return new JObject
                {
                    ["status"] = "blocked",
                    ["error"] = new JObject
                    {
                        ["code"] = "COM_ATTACH_FAILED",
                        ["message"] = readiness.ContainsKey("launchError")
                            ? Convert.ToString(readiness["launchError"])
                            : "SolidWorks could not be attached."
                    },
                    ["lifecycle"] = lifecycle
                };
            }

            JObject result;
            JObject cleanup;
            try
            {
                result = new DimensionPlanningHandoffExecutor(_solidWorks)
                    .Execute(request);
            }
            finally
            {
                cleanup = ownsSession
                    ? CleanupOwnedSolidWorksSession()
                    : SkippedCleanup("A pre-existing session was preserved.");
            }
            lifecycle["cleanup"] = cleanup;
            result["lifecycle"] = lifecycle;
            if (ownsSession && !String.Equals(cleanup.Value<string>("status"),
                    "pass", StringComparison.Ordinal))
                result["status"] = "cleanup_blocked";
            return result;
        }
    }
}
