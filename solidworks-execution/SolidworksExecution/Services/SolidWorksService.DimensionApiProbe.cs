using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    public partial class SolidWorksService
    {
        public JObject RunDimensionApiProbe(DimensionApiProbeRequest request,
            JObject sourceRequest)
        {
            if (!EnsureConnected())
                return new JObject
                {
                    ["status"] = "blocked",
                    ["error"] = new JObject
                    {
                        ["code"] = "COM_ATTACH_FAILED",
                        ["message"] =
                            "SolidWorks process not found or COM not registered."
                    }
                };

            var executor = new DimensionApiProbeExecutor(_solidWorks);
            return executor.Execute(request, sourceRequest);
        }
    }
}
