using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Web.Http;
using SolidworksExecution.Infrastructure;
using SolidworksExecution.Models;
using SolidworksExecution.Services;

namespace SolidworksExecution.Controllers
{
    [RoutePrefix("host")]
    public sealed class HostBootstrapController : ApiController
    {
        private readonly HostBootstrapRunner _runner = new HostBootstrapRunner();

        [HttpPost]
        [Route("bootstrap")]
        public HttpResponseMessage Bootstrap([FromBody] HostBootstrapRequest request)
        {
            try
            {
                HostBootstrapResponse response = _runner.Run(request);
                ExecLog.Write(
                    "<- host/bootstrap " + response.Status + " mode=" + response.Mode +
                    " report=" + response.ReportPath);
                return Request.CreateResponse(HttpStatusCode.OK, response);
            }
            catch (ArgumentException exception)
            {
                return Request.CreateResponse(HttpStatusCode.BadRequest, new
                {
                    status = "blocked",
                    error = new { code = "HOST_BOOTSTRAP_REQUEST_INVALID", message = exception.Message }
                });
            }
            catch (Exception exception)
            {
                string code = exception is TimeoutException
                    ? "HOST_BOOTSTRAP_TIMEOUT"
                    : exception is FileNotFoundException
                        ? "HOST_BOOTSTRAP_HELPER_MISSING"
                        : "HOST_BOOTSTRAP_FAILED";
                ExecLog.Write("<- host/bootstrap BLOCKED " + code + ": " + exception.Message);
                return Request.CreateResponse(HttpStatusCode.InternalServerError, new
                {
                    status = "blocked",
                    error = new { code = code, message = exception.Message }
                });
            }
        }
    }
}
