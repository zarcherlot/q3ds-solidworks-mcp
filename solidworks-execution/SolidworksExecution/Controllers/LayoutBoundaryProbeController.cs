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
    // Research-only G0 endpoint; intentionally absent from the semantic MCP.
    [RoutePrefix("api/research/layout-boundary-probe")]
    public sealed class LayoutBoundaryProbeController : ApiController
    {
        private readonly SolidWorksService _service =
            new SolidWorksService(OperationGuard.Instance);

        [HttpPost]
        [Route("")]
        public HttpResponseMessage Execute([FromBody] JObject candidate)
        {
            var contract = new LayoutBoundaryProbeContract();
            LayoutBoundaryProbeRequest request;
            LayoutBoundaryProbeContractError error;
            if (!contract.TryParse(candidate, out request, out error) ||
                !contract.TryPreflight(request, out error))
                return Request.CreateResponse(HttpStatusCode.BadRequest, new
                {
                    status = "blocked",
                    error = new
                    {
                        code = error != null ? error.Code :
                            "LAYOUT_BOUNDARY_PROBE_CONTRACT_INVALID",
                        json_pointer = error != null ? error.JsonPointer : "",
                        message = error != null ? error.Message : "invalid request"
                    }
                });
            try
            {
                JObject result = StaExecutor.Instance.Run(() =>
                    _service.RunManagedLayoutBoundaryProbe(request,
                        (JObject)candidate.DeepClone()));
                return Request.CreateResponse(HttpStatusCode.OK, result);
            }
            catch (Exception exception)
            {
                ExecLog.Write("<- layout-boundary-probe BLOCKED " +
                    exception.GetType().Name + ": " + exception.Message);
                return Request.CreateResponse(HttpStatusCode.InternalServerError, new
                {
                    status = "blocked",
                    error = new
                    {
                        code = "LAYOUT_BOUNDARY_PROBE_FAILED",
                        message = exception.Message
                    }
                });
            }
        }
    }
}
