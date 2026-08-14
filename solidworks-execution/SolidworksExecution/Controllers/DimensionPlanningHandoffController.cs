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
    // Private F1 production endpoint.  It intentionally stays outside ToolController
    // and therefore outside the Agent-visible semantic MCP surface until F6.
    [RoutePrefix("api/dimension-planning/handoff")]
    public sealed class DimensionPlanningHandoffController : ApiController
    {
        private readonly SolidWorksService _service =
            new SolidWorksService(OperationGuard.Instance);

        [HttpPost]
        [Route("")]
        public HttpResponseMessage Execute([FromBody] JObject candidate)
        {
            var contract = new DimensionPlanningHandoffContract();
            DimensionPlanningHandoffRequest request;
            DimensionPlanningHandoffContractError error;
            if (!contract.TryParse(candidate, out request, out error) ||
                !contract.TryPreflight(request, out error))
                return Request.CreateResponse(HttpStatusCode.BadRequest, new
                {
                    status = "blocked",
                    error = new
                    {
                        code = error != null ? error.Code :
                            "DIMENSION_PLANNING_HANDOFF_CONTRACT_INVALID",
                        json_pointer = error != null ? error.JsonPointer : "",
                        message = error != null ? error.Message : "invalid request"
                    }
                });
            try
            {
                JObject result = StaExecutor.Instance.Run(() =>
                    _service.RunManagedDimensionPlanningHandoff(request));
                return Request.CreateResponse(HttpStatusCode.OK, result);
            }
            catch (Exception exception)
            {
                ExecLog.Write("<- dimension-planning-handoff BLOCKED " +
                    exception.GetType().Name + ": " + exception.Message);
                return Request.CreateResponse(HttpStatusCode.InternalServerError, new
                {
                    status = "blocked",
                    error = new
                    {
                        code = "DIMENSION_PLANNING_HANDOFF_FAILED",
                        message = exception.Message
                    }
                });
            }
        }
    }
}
