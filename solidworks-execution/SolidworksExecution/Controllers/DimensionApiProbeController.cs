using System;
using System.Net;
using System.Net.Http;
using System.Web.Http;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Infrastructure;
using SolidworksExecution.Services;

namespace SolidworksExecution.Controllers
{
    // Research-only F0 endpoint. It is intentionally outside ToolController so the
    // operation cannot become part of the Agent-visible semantic MCP surface.
    [RoutePrefix("api/research/dimension-probe")]
    public sealed class DimensionApiProbeController : ApiController
    {
        private readonly SolidWorksService _service =
            new SolidWorksService(OperationGuard.Instance);

        [HttpPost]
        [Route("")]
        public HttpResponseMessage Execute([FromBody] JObject candidate)
        {
            var contract = new DimensionApiProbeContract();
            DimensionApiProbeRequest request;
            DimensionApiProbeContractError error;
            if (!contract.TryParse(candidate, out request, out error) ||
                !contract.TryPreflight(request, out error))
            {
                return Request.CreateResponse(HttpStatusCode.BadRequest, new
                {
                    status = "blocked",
                    error = new
                    {
                        code = error != null ? error.Code :
                            "DIMENSION_API_PROBE_CONTRACT_INVALID",
                        json_pointer = error != null ? error.JsonPointer : "",
                        message = error != null ? error.Message : "invalid request"
                    }
                });
            }

            try
            {
                JObject result = StaExecutor.Instance.Run(() =>
                    _service.RunDimensionApiProbe(request,
                        (JObject)candidate.DeepClone()));
                return Request.CreateResponse(HttpStatusCode.OK, result);
            }
            catch (Exception exception)
            {
                ExecLog.Write("<- dimension-probe BLOCKED " +
                    exception.GetType().Name + ": " + exception.Message);
                return Request.CreateResponse(HttpStatusCode.InternalServerError,
                    new
                    {
                        status = "blocked",
                        error = new
                        {
                            code = "DIMENSION_API_PROBE_FAILED",
                            message = exception.Message
                        }
                    });
            }
        }
    }
}
