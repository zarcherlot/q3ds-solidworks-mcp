using SolidworksExecution.Infrastructure;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    // Supplies only the non-COM members used by SolidWorksService.ViewPlan.cs so the production
    // partial service entry is compiled and executed by the offline contract test project.
    public partial class SolidWorksService
    {
        private readonly IOperationGuard _guard;

        public SolidWorksService(IOperationGuard guard)
        {
            _guard = guard;
        }

        private ExecutionResponse BuildFailed(string operationId, int stateVersion,
            string code, string message)
        {
            return new ExecutionResponse
            {
                OperationId = operationId,
                Status = "FAILED",
                Verified = false,
                StateVersion = stateVersion,
                Error = new ExecutionError { Code = code, Message = message }
            };
        }
    }
}
