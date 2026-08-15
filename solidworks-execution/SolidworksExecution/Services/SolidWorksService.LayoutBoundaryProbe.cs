using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    public partial class SolidWorksService
    {
        public JObject RunManagedLayoutBoundaryProbe(LayoutBoundaryProbeRequest request,
            JObject sourceRequest)
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
                ["owned_process_id"] = ownsSession
                    ? (JToken)ownedProcessId : JValue.CreateNull()
            };
            if (!attached)
            {
                lifecycle["cleanup"] = ownsSession
                    ? CleanupOwnedSolidWorksSession()
                    : SkippedCleanup("SolidWorks was not attached and no owned session was recorded.");
                return new JObject
                {
                    ["status"] = "blocked",
                    ["error"] = new JObject
                    {
                        ["code"] = "COM_ATTACH_FAILED",
                        ["message"] = readiness.ContainsKey("launchError")
                            ? Convert.ToString(readiness["launchError"])
                            : "SolidWorks could not be attached or launched."
                    },
                    ["lifecycle"] = lifecycle
                };
            }
            JObject result = null;
            JObject cleanup = null;
            try { result = RunLayoutBoundaryProbe(request, sourceRequest); }
            finally
            {
                cleanup = ownsSession ? CleanupOwnedSolidWorksSession()
                    : SkippedCleanup("A pre-existing session was preserved.");
            }
            lifecycle["cleanup"] = cleanup;
            result["lifecycle"] = lifecycle;
            if (ownsSession && !String.Equals(cleanup.Value<string>("status"), "pass",
                    StringComparison.Ordinal))
            {
                result["status"] = "cleanup_blocked";
                result["cleanup_failure"] = cleanup.DeepClone();
            }
            return result;
        }

        public JObject RunLayoutBoundaryProbe(LayoutBoundaryProbeRequest request,
            JObject sourceRequest)
        {
            if (!EnsureConnected())
                return new JObject
                {
                    ["status"] = "blocked",
                    ["error"] = new JObject
                    {
                        ["code"] = "COM_ATTACH_FAILED",
                        ["message"] = "SolidWorks process not found or COM not registered."
                    }
                };
            return new LayoutBoundaryProbeExecutor(_solidWorks).Execute(request,
                sourceRequest);
        }
    }
}
